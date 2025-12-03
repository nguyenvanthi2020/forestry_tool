# -*- coding: utf-8 -*-
from qgis.PyQt import QtCore, QtWidgets
from qgis.PyQt.QtWidgets import QDockWidget, QFileDialog, QMessageBox, QProgressBar
from qgis.PyQt.QtGui import QColor
from qgis.utils import iface
from qgis.core import (
    QgsProject, QgsCoordinateReferenceSystem, QgsRectangle,
    QgsWkbTypes, QgsGeometry, QgsRasterLayer
)
from qgis.gui import QgsProjectionSelectionWidget, QgsMapTool, QgsRubberBand

import os, io, zipfile, time

from ..gee_utils import (
    ensure_ee_initialized, ee_dem_image,
    qrect_to_ee_geometry, 
    DATASETS, estimate_pixel_count, DIRECT_DOWNLOAD_PIXEL_LIMIT,
    try_resolve_existing_drive_folder_name
)

_DRIVE_FOLDER_DEFAULT = "GEE_DEM"

class GEEDemDockWidget(QDockWidget):
    def __init__(self, iface_):
        super().__init__("GEE DEM Downloader")
        self.iface = iface_
        self.setObjectName("GEEDemDockWidget")

        container = QtWidgets.QWidget(); self.setWidget(container)
        vbox = QtWidgets.QVBoxLayout(container); form = QtWidgets.QFormLayout(); vbox.addLayout(form)

        self.cmbDataset = QtWidgets.QComboBox(); self.cmbDataset.addItems(list(DATASETS.keys()))
        form.addRow("Dataset:", self.cmbDataset)

        self.spinScale = QtWidgets.QSpinBox(); self.spinScale.setRange(1, 500); self.spinScale.setValue(30)
        form.addRow("Scale (m):", self.spinScale)

        self.crsWidget = QgsProjectionSelectionWidget()
        try: self.crsWidget.setCrs(QgsProject.instance().crs())
        except Exception: self.crsWidget.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        crsRow = QtWidgets.QHBoxLayout()
        crsRow.addWidget(self.crsWidget)
        btnProjectCrs = QtWidgets.QPushButton("← Dùng CRS dự án")
        btnProjectCrs.clicked.connect(lambda: self.crsWidget.setCrs(QgsProject.instance().crs()))
        crsRow.addWidget(btnProjectCrs)
        crsWrap = QtWidgets.QWidget(); crsWrap.setLayout(crsRow)
        form.addRow("CRS:", crsWrap)

        self.chkClip = QtWidgets.QCheckBox("Clip theo AOI"); self.chkClip.setChecked(True)
        form.addRow(self.chkClip)

        aoiRow = QtWidgets.QHBoxLayout()
        self.btnAOIExtent    = QtWidgets.QPushButton("AOI = extent hiện thời")
        self.btnAOISelection = QtWidgets.QPushButton("AOI = bbox selection")
        self.btnAOIDraw      = QtWidgets.QPushButton("AOI = vẽ khung trên canvas")
        for b in (self.btnAOIExtent, self.btnAOISelection, self.btnAOIDraw): aoiRow.addWidget(b)
        aoiWrap = QtWidgets.QWidget(); aoiWrap.setLayout(aoiRow)
        form.addRow("AOI:", aoiWrap)

        self.radDirect = QtWidgets.QRadioButton("Tải trực tiếp (getDownloadURL)")
        self.radDrive  = QtWidgets.QRadioButton("Xuất Google Drive")
        self.radDirect.setChecked(True)
        form.addRow(self.radDirect); form.addRow(self.radDrive)

        pathRow = QtWidgets.QHBoxLayout(); self.txtOut = QtWidgets.QLineEdit(); self.btnBrowse = QtWidgets.QPushButton("…")
        pathRow.addWidget(self.txtOut); pathRow.addWidget(self.btnBrowse)
        pathWrap = QtWidgets.QWidget(); pathWrap.setLayout(pathRow)
        form.addRow("Lưu GeoTIFF:", pathWrap)

        btnRow = QtWidgets.QHBoxLayout()
        self.btnPreview = QtWidgets.QPushButton("Preview (trong QGIS)")
        self.btnDownload = QtWidgets.QPushButton("Download")
        btnRow.addWidget(self.btnPreview); btnRow.addWidget(self.btnDownload)
        btnWrap = QtWidgets.QWidget(); btnWrap.setLayout(btnRow); vbox.addWidget(btnWrap)

        # Progress bar & log
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        vbox.addWidget(self.progress)
        self.log = QtWidgets.QPlainTextEdit(); self.log.setReadOnly(True); vbox.addWidget(self.log, 1)

        self._aoi_ring = None
        self._preview_layer_id = None
        self._rect_tool = None

        self.btnBrowse.clicked.connect(self._browse)
        self.btnAOIExtent.clicked.connect(self._aoi_from_canvas_extent)
        self.btnAOISelection.clicked.connect(self._aoi_from_layer_selection)
        self.btnAOIDraw.clicked.connect(self._aoi_from_draw_rect)
        self.btnPreview.clicked.connect(self._preview_qgis)
        self.btnDownload.clicked.connect(self._download)

    # ---------- AOI ----------
    def _browse(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Chọn GeoTIFF", "", "GeoTIFF (*.tif *.tiff)")
        if fn:
            if not fn.lower().endswith((".tif", ".tiff")): fn += ".tif"
            self.txtOut.setText(fn)

    def _aoi_from_canvas_extent(self):
        try:
            canvas = self.iface.mapCanvas(); rect = canvas.extent()
            if rect.isEmpty(): QMessageBox.information(self, "AOI", "Extent hiện thời rỗng."); return
            crs = canvas.mapSettings().destinationCrs()
            _geom, ring = qrect_to_ee_geometry(rect, crs)
            self._aoi_ring = ring; self._log("AOI = extent hiện thời.")
        except Exception as e: QMessageBox.warning(self, "AOI", str(e))

    def _aoi_from_layer_selection(self):
        try:
            layer = self.iface.activeLayer()
            if (layer is None) or (layer.selectedFeatureCount() == 0):
                QMessageBox.information(self, "AOI", "Chọn đối tượng trên lớp vector (dùng bbox)."); return
            bbox = None
            for f in layer.selectedFeatures():
                g = f.geometry()
                if not g or g.isEmpty(): continue
                bbox = g.boundingBox() if bbox is None else bbox.combineExtentWith(g.boundingBox())
            if bbox is None or bbox.isEmpty():
                QMessageBox.information(self, "AOI", "Không lấy được bbox."); return
            _geom, ring = qrect_to_ee_geometry(bbox, layer.crs())
            self._aoi_ring = ring; self._log("AOI = bbox selection.")
        except Exception as e: QMessageBox.warning(self, "AOI", str(e))

    def _aoi_from_draw_rect(self):
        try:
            if self._rect_tool is None:
                self._rect_tool = _RectCaptureTool(self.iface.mapCanvas(), finished_cb=self._on_rect_finished)
            self.iface.mapCanvas().setMapTool(self._rect_tool)
            self._log("Chế độ vẽ AOI: chọn 2 góc khung.")
        except Exception as e: QMessageBox.warning(self, "AOI", str(e))

    def _on_rect_finished(self, rect: QgsRectangle):
        try:
            if not rect or rect.isEmpty():
                QMessageBox.information(self, "AOI", "Khung vẽ rỗng."); return
            rect.normalize()
            crs = self.iface.mapCanvas().mapSettings().destinationCrs()
            _geom, ring = qrect_to_ee_geometry(rect, crs)
            self._aoi_ring = ring; self._log("AOI = khung vẽ.")
        except Exception as e: QMessageBox.warning(self, "AOI", str(e))

    # ---------- Preview ----------
    def _preview_qgis(self):
        try:
            ee = ensure_ee_initialized()
            ds = self.cmbDataset.currentText()
            img = ee_dem_image(ds)
            if self.chkClip.isChecked() and self._aoi_ring:
                import ee
                img = img.clip(ee.Geometry.Polygon(self._aoi_ring))

            vis = {"min": 0, "max": 4000, "palette": ["000000", "ffffff"]}
            m = img.getMapId(vis); tile_url = m["tile_fetcher"].url_format
            if self._preview_layer_id:
                lyr_old = QgsProject.instance().mapLayer(self._preview_layer_id)
                if lyr_old: QgsProject.instance().removeMapLayer(lyr_old.id())
                self._preview_layer_id = None
            uri = f"type=xyz&url={tile_url}"
            lyr = QgsRasterLayer(uri, "GEE DEM Preview", "wms")
            if not lyr.isValid(): QMessageBox.warning(self, "Preview", "Không tạo được layer XYZ tile."); return
            QgsProject.instance().addMapLayer(lyr); self._preview_layer_id = lyr.id()
            self._log("Đã thêm 'GEE DEM Preview'.")
        except Exception as e:
            QMessageBox.warning(self, "Preview", str(e))

    # ---------- Download ----------
    def _set_progress(self, v: int):
        self.progress.setValue(max(0, min(100, int(v))))
        QtWidgets.QApplication.processEvents()

    def _enable_ui(self, enabled: bool):
        for w in [self.cmbDataset, self.spinScale, self.crsWidget, self.chkClip,
                  self.btnAOIExtent, self.btnAOISelection, self.btnAOIDraw,
                  self.radDirect, self.radDrive, self.txtOut, self.btnBrowse,
                  self.btnPreview, self.btnDownload]:
            w.setEnabled(enabled)
        QtWidgets.QApplication.processEvents()

    def _download(self):
        try:
            self._enable_ui(False); self._set_progress(0)
            ee = ensure_ee_initialized()
            ds = self.cmbDataset.currentText()
            img = ee_dem_image(ds)
            scale = int(self.spinScale.value())
            crs_authid = self.crsWidget.crs().authid() or "EPSG:4326"

            region_ring = None
            px = float("inf")
            if self.chkClip.isChecked() and self._aoi_ring:
                region_ring = self._aoi_ring
                rect = self.iface.mapCanvas().extent()
                px = estimate_pixel_count(rect, self.iface.mapCanvas().mapSettings().destinationCrs(), scale)

            if self.radDrive.isChecked():
                folder = self._resolve_drive_folder_name_safe(_DRIVE_FOLDER_DEFAULT)
                self._export_drive_with_progress(img, region_ring, scale, crs_authid, folder)
                self._enable_ui(True); return

            # Direct
            out = self.txtOut.text().strip()
            if not out:
                QMessageBox.information(self, "Lưu file", "Hãy chọn đường dẫn GeoTIFF."); self._enable_ui(True); return
            if region_ring is None:
                QMessageBox.information(self, "Thiếu AOI", "Tải trực tiếp yêu cầu AOI."); self._enable_ui(True); return

            params1 = {"scale": scale, "crs": crs_authid, "region": region_ring}
            params2 = {"scale": scale, "region": region_ring}

            try:
                url = img.getDownloadURL(params1)
            except Exception as e1:
                msg = str(e1)
                too_large = ("Total request size" in msg) or ("Request Entity Too Large" in msg) or ("too large" in msg.lower())
                if too_large:
                    if px > DIRECT_DOWNLOAD_PIXEL_LIMIT:
                        ans = QMessageBox.question(self, "Vùng lớn",
                            "AOI có vẻ lớn cho tải trực tiếp. Xuất sang Google Drive?",
                            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                        if ans == QMessageBox.Yes:
                            folder = self._resolve_drive_folder_name_safe(_DRIVE_FOLDER_DEFAULT)
                            self._export_drive_with_progress(img, region_ring, scale, crs_authid, folder)
                        self._enable_ui(True); return
                    try:
                        url = img.getDownloadURL(params2)
                    except Exception:
                        ans = QMessageBox.question(self, "Request quá lớn",
                            "Tải trực tiếp vẫn quá lớn. Xuất sang Google Drive?",
                            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                        if ans == QMessageBox.Yes:
                            folder = self._resolve_drive_folder_name_safe(_DRIVE_FOLDER_DEFAULT)
                            self._export_drive_with_progress(img, region_ring, scale, crs_authid, folder)
                        self._enable_ui(True); return
                else:
                    QMessageBox.critical(self, "Lỗi tải trực tiếp", msg); self._enable_ui(True); return

            self._log("Đang tải (direct)…")
            self._set_progress(5)
            import requests
            r = requests.get(url, stream=True)
            if r.status_code != 200:
                try: preview = r.text[:500]
                except Exception: preview = f"HTTP {r.status_code}"
                QMessageBox.critical(self, "Lỗi tải trực tiếp", preview); self._enable_ui(True); return

            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            buf = io.BytesIO()
            for chunk in r.iter_content(chunk_size=1 << 20):
                if not chunk: continue
                buf.write(chunk); done += len(chunk)
                if total > 0:
                    self._set_progress(min(int(done * 100 / total), 95))
                else:
                    # tick dần nếu không biết total
                    p = min(95, int((buf.tell() / (50 * 1024 * 1024)) * 100))
                    self._set_progress(p)

            data = buf.getvalue()
            if data[:4] == b"PK\x03\x04":
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    tifs = [n for n in z.namelist() if n.lower().endswith((".tif", ".tiff"))]
                    if not tifs: QMessageBox.critical(self, "ZIP lỗi", "ZIP không chứa GeoTIFF."); self._enable_ui(True); return
                    with z.open(tifs[0]) as tif_src, open(out, "wb") as f_out:
                        f_out.write(tif_src.read())
            else:
                with open(out, "wb") as f: f.write(data)

            with open(out, "rb") as f: sig = f.read(4)
            if sig not in (b"II*\x00", b"MM\x00*"):
                QMessageBox.critical(self, "Tệp không hợp lệ", "File không phải GeoTIFF."); self._enable_ui(True); return

            self._set_progress(100)
            self._log("Đã lưu: " + out)
            if os.path.exists(out):
                self.iface.addRasterLayer(out, os.path.basename(out))
            self._enable_ui(True)

        except Exception as e:
            QMessageBox.critical(self, "Download", str(e))
            self._enable_ui(True)

    def _export_drive_with_progress(self, img, region_ring, scale, crs_authid, folder_name_or_none):
        import ee
        prefix = os.path.splitext(os.path.basename(self.txtOut.text().strip() or "gee_dem"))[0]
        #region_poly = polygon_from_ring(region_ring) if region_ring else None

        kwargs = dict(image=img, description=prefix, fileNamePrefix=prefix,
                      region=region_ring, scale=scale, crs=crs_authid,
                      fileFormat="GeoTIFF", maxPixels=1e12)
        if folder_name_or_none:
            kwargs["folder"] = folder_name_or_none

        task = ee.batch.Export.image.toDrive(**kwargs)
        task.start()
        fol_info = folder_name_or_none or "ROOT (My Drive)"
        self._log(f"Export Google Drive (thư mục: {fol_info})…")
        # Poll tiến trình (EE không có % thật, hiển thị giả lập)
        steps = 180
        for i in range(steps):
            st = task.status()
            state = st.get("state", "")
            self._log(f"[{i}] {state}")
            if state in ("COMPLETED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"):
                break
            self._set_progress(int(i * 95 / steps))
            time.sleep(2)

        st = task.status()
        if st.get("state") == "COMPLETED":
            self._set_progress(100)
            self._log("Export hoàn tất trên Google Drive.")
        else:
            self._log("Export chưa hoàn tất: " + str(st))

    def _resolve_drive_folder_name_safe(self, desired: str):
        desired = (desired or "").strip()
        if not desired: return None
        resolved = try_resolve_existing_drive_folder_name(desired)
        if resolved:
            self._log(f"Sẽ ghi vào thư mục Drive đã có: {resolved}")
        else:
            self._log("Không xác định được thư mục Drive duy nhất. Sẽ ghi vào ROOT.")
        return resolved

    def _log(self, s): self.log.appendPlainText(str(s))

# ---------- Tool vẽ AOI ----------
class _RectCaptureTool(QgsMapTool):
    def __init__(self, canvas, finished_cb):
        super().__init__(canvas)
        self.canvas = canvas
        self.finished_cb = finished_cb
        self.rubber = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.rubber.setColor(QColor(0, 153, 255, 120))
        self.rubber.setWidth(1)
        self._p1 = None
        self._active = False

    def canvasPressEvent(self, e):
        self._p1 = self.canvas.getCoordinateTransform().toMapCoordinates(e.pos())
        self._active = True
        self._update_rubber(self._p1, self._p1)

    def canvasMoveEvent(self, e):
        if not self._active or self._p1 is None: return
        p2 = self.canvas.getCoordinateTransform().toMapCoordinates(e.pos())
        self._update_rubber(self._p1, p2)

    def canvasReleaseEvent(self, e):
        if not self._active or self._p1 is None: return
        p2 = self.canvas.getCoordinateTransform().toMapCoordinates(e.pos())
        rect = QgsRectangle(self._p1, p2); rect.normalize()
        self._update_rubber(self._p1, p2); self._active = False
        if callable(self.finished_cb): self.finished_cb(rect)
        self.canvas.unsetMapTool(self); self._p1 = None

    def deactivate(self):
        super().deactivate()
        self.rubber.reset(QgsWkbTypes.PolygonGeometry)

    def _update_rubber(self, p1, p2):
        rect = QgsRectangle(p1, p2); rect.normalize()
        self.rubber.setToGeometry(QgsGeometry.fromRect(rect), None)
