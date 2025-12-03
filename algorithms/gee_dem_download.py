# -*- coding: utf-8 -*-
from qgis.PyQt import QtCore, QtWidgets
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterEnum,
    QgsProcessingParameterExtent, QgsProcessingParameterCrs,
    QgsProcessingParameterNumber, QgsProcessingParameterBoolean,
    QgsProcessingParameterFileDestination
)
from qgis.utils import iface

from ..gee_utils import (
    ensure_ee_initialized, DATASETS, ee_dem_image,
    qrect_to_ee_geometry,estimate_pixel_count, DIRECT_DOWNLOAD_PIXEL_LIMIT,
    try_resolve_existing_drive_folder_name
)

import os, io, zipfile, time

_DRIVE_FOLDER_DEFAULT = "GEE_DEM"

# ---- Hỏi Yes/No an toàn GUI (không treo) ----
class _GuiAsk(QtCore.QObject):
    finished = QtCore.pyqtSignal(int)
    @QtCore.pyqtSlot(str, str, bool)
    def ask(self, title, text, default_yes):
        ans = QtWidgets.QMessageBox.question(
            iface.mainWindow(), title, text,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes if default_yes else QtWidgets.QMessageBox.No
        ); self.finished.emit(ans)

def _ask_yes_no_gui(title, text, default_yes=True):
    app = QtWidgets.QApplication.instance()
    runner = _GuiAsk(); runner.moveToThread(app.thread())
    loop = QtCore.QEventLoop(); out = {"ans": QtWidgets.QMessageBox.No}
    runner.finished.connect(lambda v: (out.update(ans=v), loop.quit()))
    QtCore.QMetaObject.invokeMethod(
        runner, "ask", QtCore.Qt.QueuedConnection,
        QtCore.Q_ARG(str, title), QtCore.Q_ARG(str, text), QtCore.Q_ARG(bool, bool(default_yes))
    )
    loop.exec_(); return out["ans"]

class GEEDemDownloadAlg(QgsProcessingAlgorithm):
    P_DATASET='DATASET'; P_EXTENT='EXTENT'; P_CRS='CRS'; P_SCALE='SCALE'
    P_CLIP='CLIP'; P_METHOD='METHOD'; P_FILL_ND='FILL_NODATA'; P_FILL_VAL='FILL_VALUE'
    P_OUT='OUTPUT'

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(self.P_DATASET, "Dataset DEM",
            options=list(DATASETS.keys()), defaultValue=0))
        self.addParameter(QgsProcessingParameterExtent(self.P_EXTENT,
            "Extent (bỏ trống để dùng extent canvas)", optional=True))
        self.addParameter(QgsProcessingParameterCrs(self.P_CRS, "CRS xuất raster",
            defaultValue="EPSG:4326"))
        self.addParameter(QgsProcessingParameterNumber(self.P_SCALE, "Scale (m)",
            type=QgsProcessingParameterNumber.Integer, defaultValue=30, minValue=1, maxValue=500))
        self.addParameter(QgsProcessingParameterBoolean(self.P_CLIP, "Clip theo extent/AOI", defaultValue=True))
        self.addParameter(QgsProcessingParameterEnum(self.P_METHOD, "Phương thức",
            options=["Tải trực tiếp (getDownloadURL)", "Xuất Google Drive"], defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(self.P_FILL_ND, "Gán NoData (unmask)", defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(self.P_FILL_VAL, "Giá trị NoData để gán",
            type=QgsProcessingParameterNumber.Double, defaultValue=-9999))
        self.addParameter(QgsProcessingParameterFileDestination(self.P_OUT, "Lưu GeoTIFF (chỉ cho tải trực tiếp)",
            fileFilter="GeoTIFF (*.tif *.tiff)"))

    def processAlgorithm(self, parameters, context, feedback):
        import ee, requests
        ee = ensure_ee_initialized()

        ds_key = list(DATASETS.keys())[self.parameterAsEnum(parameters, self.P_DATASET, context)]
        scale = int(self.parameterAsInt(parameters, self.P_SCALE, context))
        crs = self.parameterAsCrs(parameters, self.P_CRS, context).authid()
        clip = self.parameterAsBool(parameters, self.P_CLIP, context)
        method = self.parameterAsEnum(parameters, self.P_METHOD, context)
        fill_nd = self.parameterAsBool(parameters, self.P_FILL_ND, context)
        fill_val = self.parameterAsDouble(parameters, self.P_FILL_VAL, context)
        out_path = self.parameterAsFileOutput(parameters, self.P_OUT, context)

        # AOI
        canvas = iface.mapCanvas()
        rect_param = self.parameterAsExtent(parameters, self.P_EXTENT, context)
        rect = canvas.extent() if (rect_param.isEmpty() and clip) else (rect_param if not rect_param.isEmpty() else canvas.extent())
        src_crs = canvas.mapSettings().destinationCrs()

        img = ee_dem_image(ds_key)
        if fill_nd: img = img.unmask(fill_val)

        region_ring = None
        px = float("inf")
        if clip:
            _geom, region_ring = qrect_to_ee_geometry(rect, src_crs)
            px = estimate_pixel_count(rect, src_crs, scale)
        feedback.pushInfo(f"Pixels≈{px:.0f}")

        # Xuất Drive ngay
        if method == 1:
            folder = self._resolve_drive_folder_name_safe(_DRIVE_FOLDER_DEFAULT)
            self._export_to_drive_with_progress(ee, img, region_ring, scale, crs, out_path, folder, feedback)
            return { self.P_OUT: "" }

        # Direct
        if region_ring is None:
            raise Exception("Tải trực tiếp yêu cầu AOI. Bật 'Clip theo extent/AOI' hoặc chọn 'Xuất Google Drive'.")
        if not out_path:
            raise Exception("Bạn phải chọn đường dẫn lưu GeoTIFF.")

        params1 = {"scale": scale, "crs": crs, "region": region_ring}  # dùng LinearRing
        params2 = {"scale": scale, "region": region_ring}

        url = None
        try:
            url = img.getDownloadURL(params1)
        except Exception as e1:
            msg = str(e1)
            too_large = ("Total request size" in msg) or ("Request Entity Too Large" in msg) or ("too large" in msg.lower())
            if too_large:
                if px > DIRECT_DOWNLOAD_PIXEL_LIMIT:
                    ans = _ask_yes_no_gui("Vùng lớn", "AOI có vẻ lớn cho tải trực tiếp. Xuất sang Google Drive?", True)
                    if ans == QtWidgets.QMessageBox.Yes:
                        folder = self._resolve_drive_folder_name_safe(_DRIVE_FOLDER_DEFAULT)
                        self._export_to_drive_with_progress(ee, img, region_ring, scale, crs, out_path, folder, feedback)
                        return { self.P_OUT: "" }
                    else:
                        raise Exception("Đã dừng theo yêu cầu (không xuất Google Drive).")
                try:
                    url = img.getDownloadURL(params2)
                except Exception:
                    ans = _ask_yes_no_gui("Request quá lớn", "Tải trực tiếp vẫn quá lớn. Xuất sang Google Drive?", True)
                    if ans == QtWidgets.QMessageBox.Yes:
                        folder = self._resolve_drive_folder_name_safe(_DRIVE_FOLDER_DEFAULT)
                        self._export_to_drive_with_progress(ee, img, region_ring, scale, crs, out_path, folder, feedback)
                        return { self.P_OUT: "" }
                    else:
                        raise Exception("Đã dừng theo yêu cầu (không xuất Google Drive).")
            else:
                raise

        feedback.pushInfo("Đang tải dữ liệu (direct)…")
        # ---- Tiến trình tải trực tiếp ----
        r = requests.get(url, stream=True)
        if r.status_code != 200:
            try: preview = r.text[:500]
            except Exception: preview = f"HTTP {r.status_code}"
            raise Exception("GEE trả lỗi khi tải trực tiếp:\n" + preview)

        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        buf = io.BytesIO()
        for chunk in r.iter_content(chunk_size=1 << 20):
            if not chunk: continue
            buf.write(chunk); done += len(chunk)
            if total > 0:
                feedback.setProgress(min(int(done * 100 / total), 99))
            else:
                # không biết tổng: tick dần
                p = min(99, int((buf.tell() / (50 * 1024 * 1024)) * 100))
                feedback.setProgress(p)
            QtWidgets.QApplication.processEvents()

        data = buf.getvalue()
        if data[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                tifs = [n for n in z.namelist() if n.lower().endswith((".tif", ".tiff"))]
                if not tifs: raise Exception("ZIP không chứa GeoTIFF.")
                with z.open(tifs[0]) as tif_src, open(out_path, "wb") as f_out:
                    f_out.write(tif_src.read())
        else:
            with open(out_path, "wb") as f:
                f.write(data)

        with open(out_path, "rb") as f:
            sig = f.read(4)
        if sig not in (b"II*\x00", b"MM\x00*"):
            raise Exception("File không phải GeoTIFF hợp lệ.")

        feedback.setProgress(100)
        feedback.pushInfo("Đã lưu: " + out_path)
        return { self.P_OUT: out_path }

    # ---- Export Drive + thanh tiến trình (poll trạng thái) ----
    def _export_to_drive_with_progress(self, ee, img, region_ring, scale, crs, out_path, folder_name_or_none, feedback):
        prefix = os.path.splitext(os.path.basename(out_path or "gee_dem"))[0]
        #region_poly = polygon_from_ring(region_ring) if region_ring else None
        if region_ring is None:
            feedback.reportError("Export Drive cần AOI (region)."); return
        kwargs = dict(image=img, description=prefix, fileNamePrefix=prefix,
                      region=region_ring, scale=scale, crs=crs,
                      fileFormat="GeoTIFF", maxPixels=1e12)
        if folder_name_or_none:
            kwargs["folder"] = folder_name_or_none

        task = ee.batch.Export.image.toDrive(**kwargs)
        task.start()
        fol_info = folder_name_or_none or "ROOT (My Drive)"
        feedback.pushInfo(f"Đã gửi job lên Google Drive (thư mục: {fol_info}).")

        # Poll tiến trình (giả lập % vì EE không trả %)
        steps = 180  # ~6 phút với 2s/step
        for i in range(steps):
            if feedback.isCanceled(): break
            st = task.status()
            state = st.get("state", "")
            feedback.pushInfo(f"[{i}] {state}")
            if state in ("COMPLETED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"):
                break
            # map i->0..95%
            feedback.setProgress(int(i * 95 / steps))
            QtWidgets.QApplication.processEvents()
            time.sleep(2)

        st = task.status()
        state = st.get("state", "")
        if state == "COMPLETED":
            feedback.setProgress(100)
            feedback.pushInfo("Export hoàn tất trên Google Drive.")
        else:
            feedback.reportError("Export Drive chưa hoàn tất: " + str(st))

    def _resolve_drive_folder_name_safe(self, desired: str):
        desired = (desired or "").strip()
        if not desired: return None
        resolved = try_resolve_existing_drive_folder_name(desired)
        return resolved  # None -> ghi ROOT

    # ---- Metadata ----
    def name(self): return "download_gee_dem"
    def displayName(self): return "Tải DEM từ Google Earth Engine"
    def group(self): return "Tiện ích Raster"
    def groupId(self): return "raster_utils"
    def shortHelpString(self):
        return (
            "<b>Tải trực tiếp</b> có thanh tiến trình; nếu request quá lớn, thuật toán hỏi xuất Google Drive "
            "và hiển thị tiến trình export (poll trạng thái).<br/>"
            "Xuất Drive: chỉ định thư mục nếu xác định được duy nhất; nếu không thì ghi ROOT để tránh tạo thư mục trùng tên."
        )
    def createInstance(self): return GEEDemDownloadAlg()
