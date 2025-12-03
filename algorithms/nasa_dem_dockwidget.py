# -*- coding: utf-8 -*-
"""
Earthdata DEM Downloader (Dock)
- CMR search: NO AUTH
- Download: AUTH (QGIS AuthConfig preferred, fallback user/pass)
- Warm-up auth (ẩn host trong code): URS/LPDAAC/CMR
- Clip: nếu bật mà không chọn AOI => dùng Extent khung nhìn
"""

from __future__ import annotations
import os
import json
import zipfile
from typing import List, Optional, Tuple

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import Qt, QSettings, QEventLoop, QObject, QUrl
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QLineEdit, QProgressBar, QFileDialog,
    QTextEdit, QGroupBox, QRadioButton
)
from qgis.PyQt.QtNetwork import (
    QNetworkAccessManager, QNetworkRequest, QNetworkReply, QAuthenticator
)

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsRectangle,
    QgsWkbTypes, QgsGeometry,
    QgsProcessingFeedback, QgsProcessingException, QgsApplication
)
from qgis.utils import iface

# Auth config selector (optional)
try:
    from qgis.gui import QgsAuthConfigSelect
    HAS_AUTH_SELECTOR = True
except Exception:
    HAS_AUTH_SELECTOR = False

# Projection selector (optional)
try:
    from qgis.gui import QgsProjectionSelectionWidget
    HAS_PROJ_SELECTOR = True
except Exception:
    HAS_PROJ_SELECTOR = False


# ------------------------------ Helpers ------------------------------ #
def _layer_by_id_or_name(name_or_id: str) -> Optional[QgsVectorLayer]:
    if not name_or_id:
        return None
    p = QgsProject.instance()
    lyr = p.mapLayer(name_or_id)
    if isinstance(lyr, QgsVectorLayer):
        return lyr
    for lyr in p.mapLayers().values():
        if isinstance(lyr, QgsVectorLayer) and lyr.name() == name_or_id:
            return lyr
    return None


def _to4326_rect(rect: QgsRectangle, src_crs: QgsCoordinateReferenceSystem) -> QgsRectangle:
    if not src_crs or not src_crs.isValid():
        src_crs = QgsCoordinateReferenceSystem('EPSG:4326')
    if src_crs.authid() == 'EPSG:4326':
        return rect
    ct = QgsCoordinateTransform(src_crs, QgsCoordinateReferenceSystem('EPSG:4326'), QgsProject.instance())
    return ct.transform(rect)


def _get_union_geom(layer: QgsVectorLayer, only_selected: bool) -> Optional[QgsGeometry]:
    if not layer or not layer.isValid():
        return None
    feats = layer.selectedFeatures() if only_selected else layer.getFeatures()
    geoms: List[QgsGeometry] = []
    for f in feats:
        g = f.geometry()
        if not g or g.isEmpty():
            continue
        if g.type() != QgsWkbTypes.PolygonGeometry:
            continue
        geoms.append(g)
    if not geoms:
        return None
    u = geoms[0]
    for g in geoms[1:]:
        try:
            u = u.combine(g)
        except Exception:
            u = u.union(g)
    return u


def _bbox_of_geom4326(geom: QgsGeometry, geom_crs: QgsCoordinateReferenceSystem) -> Optional[Tuple[float, float, float, float]]:
    if not geom:
        return None
    rect = geom.boundingBox()
    rect4326 = _to4326_rect(rect, geom_crs)
    return (rect4326.xMinimum(), rect4326.yMinimum(), rect4326.xMaximum(), rect4326.yMaximum())


# ------------------------------ Net helper ------------------------------ #
class Net(QObject):
    """
    NAM wrapper:
    - CMR search: apply_auth=False
    - Download & warm-up: apply_auth=True
    - Hỗ trợ QGIS AuthConfig (ưu tiên) + fallback user/pass
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        self.user: Optional[str] = None
        self.passwd: Optional[str] = None
        self.authcfg: Optional[str] = None
        self._allow_auth: bool = True
        self.nam.authenticationRequired.connect(self._on_auth_required)

    def _on_auth_required(self, reply: QNetworkReply, authenticator: QAuthenticator):
        if not self._allow_auth:
            return
        # Ưu tiên QGIS Auth Config
        try:
            if self.authcfg:
                QgsApplication.authManager().updateNetworkReply(reply, authenticator, self.authcfg)
                return
        except Exception:
            pass
        # Fallback basic
        if self.user and self.passwd:
            authenticator.setUser(self.user)
            authenticator.setPassword(self.passwd)

    def _prepare_req(self, qurl: QUrl, apply_auth: bool) -> QNetworkRequest:
        req = QNetworkRequest(qurl)
        req.setRawHeader(b'User-Agent', b'QGIS-Earthdata-DEM/1.0')
        # Thêm header từ AuthConfig trước (preemptive) nếu cho phép
        if apply_auth and self.authcfg:
            try:
                QgsApplication.authManager().updateNetworkRequest(req, self.authcfg)
            except Exception:
                pass
        try:
            req.setAttribute(QNetworkRequest.FollowRedirectsAttribute, True)
        except Exception:
            pass
        return req

    def _get_block(self, req: QNetworkRequest, timeout_ms: int=60000, allow_auth: bool=True) -> QNetworkReply:
        prev = self._allow_auth
        self._allow_auth = allow_auth
        try:
            reply = self.nam.get(req)
            loop = QEventLoop()
            reply.finished.connect(loop.quit)
            # simple timeout
            from qgis.PyQt.QtCore import QTimer
            timer = QTimer(); timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(timeout_ms)
            loop.exec_()
            timer.stop()
            return reply
        finally:
            self._allow_auth = prev

    def get_block(self, url: str, extra_headers: Optional[dict]=None, timeout_ms: int=60000, apply_auth: bool=True) -> QNetworkReply:
        req = self._prepare_req(QUrl(url), apply_auth=apply_auth)
        if extra_headers:
            for k, v in extra_headers.items():
                req.setRawHeader(k.encode('utf-8'), str(v).encode('utf-8'))
        return self._get_block(req, timeout_ms=timeout_ms, allow_auth=apply_auth)

    def get_follow_redirects(self, url: str, timeout_ms: int=300000, apply_auth: bool=True) -> QNetworkReply:
        max_hops = 10
        qurl = QUrl(url)
        while max_hops > 0:
            req = self._prepare_req(qurl, apply_auth=apply_auth)
            rep = self._get_block(req, timeout_ms=timeout_ms, allow_auth=apply_auth)
            redir = rep.attribute(QNetworkRequest.RedirectionTargetAttribute)
            if redir is not None:
                rep.deleteLater()
                redir_url = redir if isinstance(redir, QUrl) else QUrl(str(redir))
                if (not redir_url.isValid()) or (redir_url.scheme() == ''):
                    redir_url = req.url().resolved(redir_url)
                qurl = redir_url
                max_hops -= 1
                continue
            return rep
        return rep

    def warm_up(self, hosts: List[str], timeout_ms: int=120000):
        # Với URS: gọi authorize để buộc 401
        for u in hosts:
            if not u:
                continue
            url = u
            try:
                qurl = QUrl(url)
                host = qurl.host().lower()
                if 'urs.earthdata.nasa.gov' in host:
                    url = 'https://urs.earthdata.nasa.gov/oauth/authorize?client_id=BO_n7nTIlMljdvU6kRRB3g&redirect_uri=https://example.com'
                rep = self.get_follow_redirects(url, timeout_ms=timeout_ms, apply_auth=True)
                rep.deleteLater()
            except Exception:
                pass


# ------------------------------ Dock Widget ------------------------------ #
class EarthdataDemDock(QDockWidget):
    # Auth hosts mặc định (ẩn khỏi UI)
    _DEFAULT_AUTH_HOSTS = [
        'https://urs.earthdata.nasa.gov',
        'https://data.lpdaac.earthdatacloud.nasa.gov',
        'https://cmr.earthdata.nasa.gov'
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Tải DEM từ máy chủ NASA (Earthdata)')
        self.setObjectName('EarthdataDemDock')

        self.net = Net(self)
        self._download_urls: List[str] = []
        self._download_idx = -1
        self._current_target = None
        self._downloaded_files: List[str] = []

        w = QWidget(); self.setWidget(w)
        v = QVBoxLayout(w); v.setContentsMargins(8, 8, 8, 8)

        # Dataset
        g_ds = QGroupBox('Dữ liệu'); v.addWidget(g_ds)
        l_ds = QHBoxLayout(g_ds)
        l_ds.addWidget(QLabel('Loại DEM:'))
        self.cboDataset = QComboBox(); self.cboDataset.addItems(['NASADEM_HGT', 'SRTMGL1', 'ASTGTM'])
        l_ds.addWidget(self.cboDataset, 1)

        # AOI
        g_aoi = QGroupBox('Phạm vi tìm & cắt'); v.addWidget(g_aoi)
        l_aoi = QVBoxLayout(g_aoi)
        h1 = QHBoxLayout(); l_aoi.addLayout(h1)
        self.cboAoiLayer = QComboBox(); self._refresh_vector_layers(self.cboAoiLayer)
        h1.addWidget(QLabel('Lớp AOI (đa giác):'))
        h1.addWidget(self.cboAoiLayer, 1)
        self.chkAoiSelected = QCheckBox('Chỉ dùng đối tượng đang chọn'); h1.addWidget(self.chkAoiSelected)
        h2 = QHBoxLayout(); l_aoi.addLayout(h2)
        self.chkUseCanvas = QCheckBox('Hoặc dùng khung nhìn hiện tại')
        h2.addWidget(self.chkUseCanvas)

        # Output & options
        g_out = QGroupBox('Đầu ra (Tùy chọn)'); v.addWidget(g_out)
        l_out = QVBoxLayout(g_out)
        h3 = QHBoxLayout(); l_out.addLayout(h3)
        self.txtFolder = QLineEdit(); self.btnFolder = QPushButton('…')
        h3.addWidget(QLabel('Thư mục tải về:'))
        h3.addWidget(self.txtFolder, 1)
        h3.addWidget(self.btnFolder)
        self.btnFolder.clicked.connect(self._pick_folder)

        h4 = QHBoxLayout(); l_out.addLayout(h4)
        self.chkMosaic = QCheckBox('Ghép Mosaic (GDAL)')
        self.txtMosaic = QLineEdit(); self.txtMosaic.setPlaceholderText('mosaic.tif (mặc định trong thư mục tải về)')
        h4.addWidget(self.chkMosaic)
        h4.addWidget(QLabel('Tệp Mosaic:'))
        h4.addWidget(self.txtMosaic, 1)

        h5 = QHBoxLayout(); l_out.addLayout(h5)
        self.chkClip = QCheckBox('Cắt (AOI hoặc Extent nếu không có AOI)')
        self.cboClipAoi = QComboBox(); self._refresh_vector_layers(self.cboClipAoi)
        h5.addWidget(self.chkClip)
        h5.addWidget(QLabel('AOI để cắt:'))
        h5.addWidget(self.cboClipAoi, 1)

        # ---- Output CRS (optional) ----
        g_crs = QGroupBox('Hệ tọa độ đầu ra (tùy chọn)'); v.addWidget(g_crs)
        l_crs = QHBoxLayout(g_crs)
        self.chkSetOutCrs = QCheckBox('Đặt CRS đầu ra'); l_crs.addWidget(self.chkSetOutCrs)
        if HAS_PROJ_SELECTOR:
            self.projSel = QgsProjectionSelectionWidget(); l_crs.addWidget(self.projSel, 1)
        else:
            self.projSel = None
            self.txtOutCrs = QLineEdit(); self.txtOutCrs.setPlaceholderText('VD: EPSG:4326')
            l_crs.addWidget(self.txtOutCrs, 1)

        # ---- Output data type (default Int16) ----
        g_dt = QGroupBox('Kiểu dữ liệu đầu ra'); v.addWidget(g_dt)
        l_dt = QHBoxLayout(g_dt)
        l_dt.addWidget(QLabel('Kiểu dữ liệu:'))
        self.cboDtype = QComboBox()
        for label, ot in [
            ('Int16 (mặc định)', 'Int16'),
            ('Float32', 'Float32'),
            ('Byte', 'Byte'),
            ('UInt16', 'UInt16'),
            ('Int32', 'Int32'),
            ('Float64', 'Float64'),
            ('Giữ nguyên kiểu đầu vào', 'USE_INPUT')
        ]:
            self.cboDtype.addItem(label, ot)
        self.cboDtype.setCurrentIndex(0)
        l_dt.addWidget(self.cboDtype, 1)

        # Auth
        g_auth = QGroupBox('Xác thực Earthdata'); v.addWidget(g_auth)
        l_auth = QVBoxLayout(g_auth)
        self.optAuthBasic = QRadioButton('Tên đăng nhập/Mật khẩu')
        self.optAuthCfg = QRadioButton('QGIS Auth Config (khuyên dùng)')
        self.optAuthBasic.setChecked(True)
        h_auth_mode = QHBoxLayout(); l_auth.addLayout(h_auth_mode)
        h_auth_mode.addWidget(self.optAuthBasic)
        h_auth_mode.addWidget(self.optAuthCfg)
        h_auth_mode.addStretch(1)

        hb = QHBoxLayout(); l_auth.addLayout(hb)
        self.txtUser = QLineEdit(); self.txtPass = QLineEdit(); self.txtPass.setEchoMode(QLineEdit.Password)
        hb.addWidget(QLabel('User:'))
        hb.addWidget(self.txtUser)
        hb.addWidget(QLabel('Pass:'))
        hb.addWidget(self.txtPass)
        self.chkRemember = QCheckBox('Ghi nhớ cục bộ (QSettings)'); hb.addWidget(self.chkRemember)

        hc = QHBoxLayout(); l_auth.addLayout(hc)
        if HAS_AUTH_SELECTOR:
            self.authSel = QgsAuthConfigSelect(); hc.addWidget(QLabel('Auth Config:'))
            hc.addWidget(self.authSel, 1)
        else:
            self.authSel = None
            lab = QLabel('QGIS Auth Config không khả dụng trên bản cài này.'); lab.setStyleSheet('color:#aa0000')
            hc.addWidget(lab)

        # Run
        h_run = QHBoxLayout(); v.addLayout(h_run)
        self.btnSearch = QPushButton('Tìm tile…')
        self.btnRun = QPushButton('Tải về')
        self.btnCancel = QPushButton('Hủy'); self.btnCancel.setEnabled(False)
        h_run.addWidget(self.btnSearch)
        h_run.addWidget(self.btnRun)
        h_run.addWidget(self.btnCancel)

        self.prg = QProgressBar(); self.prg.setRange(0, 100)
        v.addWidget(self.prg)
        self.txtLog = QTextEdit(); self.txtLog.setReadOnly(True); self.txtLog.setMinimumHeight(120)
        v.addWidget(self.txtLog, 1)

        # Connections
        self.btnSearch.clicked.connect(self._search_preview)
        self.btnRun.clicked.connect(self._start_download)
        self.btnCancel.clicked.connect(self._cancel)
        self.optAuthBasic.toggled.connect(self._toggle_auth_controls)
        self.optAuthCfg.toggled.connect(self._toggle_auth_controls)
        self.chkSetOutCrs.toggled.connect(self._toggle_out_crs_controls)

        # Restore settings
        self._load_settings()
        self._toggle_auth_controls()
        self._toggle_out_crs_controls()

        QgsProject.instance().layersAdded.connect(self._refresh_all_layer_combos)
        QgsProject.instance().layersRemoved.connect(self._refresh_all_layer_combos)

    # UI helpers
    def _refresh_vector_layers(self, combo: QComboBox):
        combo.clear()
        combo.addItem('<None>', '')
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
                combo.addItem(lyr.name(), lyr.id())

    def _refresh_all_layer_combos(self, *args):
        self._refresh_vector_layers(self.cboAoiLayer)
        self._refresh_vector_layers(self.cboClipAoi)

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, 'Chọn thư mục tải về', os.path.expanduser('~'))
        if d:
            self.txtFolder.setText(d)

    def _toggle_auth_controls(self):
        basic = self.optAuthBasic.isChecked()
        for w in [self.txtUser, self.txtPass, self.chkRemember]:
            w.setEnabled(basic)
        if self.authSel:
            self.authSel.setEnabled(self.optAuthCfg.isChecked())

    def _append_log(self, msg: str):
        self.txtLog.append(msg)

    def _toggle_out_crs_controls(self):
        enable = self.chkSetOutCrs.isChecked()
        if HAS_PROJ_SELECTOR and self.projSel is not None:
            self.projSel.setEnabled(enable)
        elif hasattr(self, 'txtOutCrs'):
            self.txtOutCrs.setEnabled(enable)

    def _target_crs(self) -> Optional[QgsCoordinateReferenceSystem]:
        if not self.chkSetOutCrs.isChecked():
            return None
        if HAS_PROJ_SELECTOR and self.projSel is not None:
            crs = self.projSel.crs()
            return crs if crs and crs.isValid() else None
        auth = (self.txtOutCrs.text().strip() if hasattr(self, 'txtOutCrs') else '')
        if not auth:
            return None
        crs = QgsCoordinateReferenceSystem(auth)
        return crs if crs.isValid() else None

    def _abs_in_outfolder(self, path_str: str, default_name: str) -> str:
        p = (path_str or '').strip() or default_name
        if not p.lower().endswith(('.tif', '.tiff')):
            p += '.tif'
        if not os.path.isabs(p):
            p = os.path.join(self._out_folder, p)
        p = os.path.abspath(p)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    # AOI → bbox
    def _compute_bbox(self) -> Optional[Tuple[float, float, float, float]]:
        # Dùng extent khung nhìn nếu được chọn
        if self.chkUseCanvas.isChecked():
            rect = iface.mapCanvas().extent()
            src_crs = iface.mapCanvas().mapSettings().destinationCrs()
            rect4326 = _to4326_rect(rect, src_crs)
            return (rect4326.xMinimum(), rect4326.yMinimum(), rect4326.xMaximum(), rect4326.yMaximum())
        # Ngược lại dùng lớp AOI
        aoi_id = self.cboAoiLayer.currentData()
        aoi = _layer_by_id_or_name(aoi_id)
        if not aoi:
            return None
        geom = _get_union_geom(aoi, self.chkAoiSelected.isChecked())
        if not geom:
            return None
        return _bbox_of_geom4326(geom, aoi.crs())

    # ------------------------------ CMR search ------------------------------ #
    def _cmr_search(self, short_name: str, bbox: Tuple[float, float, float, float]) -> List[str]:
        west, south, east, north = bbox
        url = (
            'https://cmr.earthdata.nasa.gov/search/granules.json?'
            f'short_name={short_name}&bounding_box={west},{south},{east},{north}&page_size=2000&sort_key=start_date'
        )
        # CMR là public: apply_auth=False
        rep = self.net.get_block(url, timeout_ms=60000, apply_auth=False)
        if rep.error() != QNetworkReply.NoError:
            raise RuntimeError(rep.errorString())
        data = bytes(rep.readAll()); rep.deleteLater()
        try:
            j = json.loads(data.decode('utf-8'))
        except Exception as e:
            raise RuntimeError(f'Lỗi parse JSON CMR: {e}')
        entries = (j.get('feed', {}) or {}).get('entry', []) or []
        urls: List[str] = []
        for it in entries:
            for l in it.get('links', []) or []:
                href = l.get('href'); rel = l.get('rel', '')
                if not href:
                    continue
                if href.startswith('s3://'):
                    continue
                if ('data#' in rel) or rel.endswith('/data') or href.lower().endswith(('.zip', '.hgt', '.tif', '.tiff')):
                    urls.append(href)
        urls = [u for u in urls if u.lower().endswith(('.hgt', '.tif', '.tiff', '.zip'))]
        seen = set(); uniq = []
        for u in urls:
            if u not in seen:
                uniq.append(u); seen.add(u)
        return uniq

    def _search_preview(self):
        bbox = self._compute_bbox()
        if not bbox:
            QtWidgets.QMessageBox.warning(self, 'Thiếu AOI', 'Hãy chọn lớp AOI hoặc bật dùng extent khung nhìn.')
            return
        self._append_log(f'AOI bbox (W,S,E,N): {bbox[0]:.6f}, {bbox[1]:.6f}, {bbox[2]:.6f}, {bbox[3]:.6f}')
        try:
            urls = self._cmr_search(self.cboDataset.currentText(), bbox)
            n = len(urls)
            self._append_log(f'Tìm thấy {n} liên kết dữ liệu tiềm năng.')
            if n > 0:
                self._append_log('\n'.join(['  • ' + os.path.basename(u) for u in urls[:20]]) + ('\n  …' if n > 20 else ''))
        except Exception as e:
            self._append_log(f'Lỗi tìm kiếm: {e}')

    # ------------------------------ Download flow ------------------------------ #
    def _start_download(self):
        bbox = self._compute_bbox()
        if not bbox:
            QtWidgets.QMessageBox.warning(self, 'Thiếu AOI', 'Hãy chọn lớp AOI hoặc bật dùng extent khung nhìn.')
            return
        out_folder = self.txtFolder.text().strip()
        if not out_folder:
            QtWidgets.QMessageBox.warning(self, 'Thiếu thư mục', 'Hãy chọn thư mục tải về.')
            return
        self.do_mosaic = self.chkMosaic.isChecked()
        self.do_clip = self.chkClip.isChecked()
        if self.do_clip and not self.do_mosaic and len(self.cboClipAoi.currentData() or '') == 0:
            QtWidgets.QMessageBox.information(self, 'Khuyến nghị', 'Bạn đang cắt mà không ghép Mosaic; nếu có nhiều tile sẽ không cắt được. Nên bật Mosaic.')
        self.mosaic_path = self.txtMosaic.text().strip() or None

        # Save auth into net helper
        if self.optAuthCfg.isChecked() and HAS_AUTH_SELECTOR and self.authSel is not None:
            self.net.authcfg = self.authSel.configId() or None
            self.net.user = None; self.net.passwd = None
        else:
            self.net.authcfg = None
            self.net.user = self.txtUser.text().strip() or None
            self.net.passwd = self.txtPass.text() or None

        # Save settings
        self._save_settings()

        # Warm-up auth (ẩn host trong UI, chỉ log ngắn)
        self._append_log('Khởi tạo xác thực Earthdata…')
        try:
            self.net.warm_up(self._DEFAULT_AUTH_HOSTS, timeout_ms=120000)
        except Exception:
            pass

        # Search now (blocking) to lock list of files to download (no auth)
        try:
            self._download_urls = self._cmr_search(self.cboDataset.currentText(), bbox)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Lỗi tìm CMR', str(e))
            return
        if not self._download_urls:
            self._append_log('Không có tệp cần tải.')
            return

        self._downloaded_files = []
        self._download_idx = -1
        self._out_folder = out_folder

        # UI state
        self.btnRun.setEnabled(False)
        self.btnSearch.setEnabled(False)
        self.btnCancel.setEnabled(True)
        self.prg.setValue(0)
        self._append_log('Bắt đầu tải…')
        self._kick_next()

    def _kick_next(self):
        # Next file
        self._download_idx += 1
        if self._download_idx >= len(self._download_urls):
            # Done: process
            self._after_all_downloads()
            return
        url = self._download_urls[self._download_idx]
        base = os.path.basename(url.split('?')[0])
        self._current_target = os.path.join(self._out_folder, base)
        self._append_log(f'— Tải ({self._download_idx+1}/{len(self._download_urls)})…')

        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b'User-Agent', b'QGIS-Earthdata-DEM/1.0')
        # Thêm AuthConfig preemptive cho request download
        if self.net.authcfg:
            try:
                QgsApplication.authManager().updateNetworkRequest(req, self.net.authcfg)
            except Exception:
                pass
        try:
            # Qt >= 5.9 supports auto-follow redirects
            req.setAttribute(QNetworkRequest.FollowRedirectsAttribute, True)
        except Exception:
            pass
        rep = self.net.nam.get(req)
        rep.downloadProgress.connect(self._on_progress)
        rep.finished.connect(lambda r=rep: self._on_reply_finished(r))

    def _on_progress(self, done: int, total: int):
        if total > 0:
            frac = done/total
            overall = int(((self._download_idx) + frac) / max(1, len(self._download_urls)) * 70)
            self.prg.setValue(overall)

    def _on_reply_finished(self, reply: QNetworkReply):
        # Handle redirect
        redir = reply.attribute(QNetworkRequest.RedirectionTargetAttribute)
        if redir is not None:
            # Build absolute redirect URL (handle relative Location headers)
            if isinstance(redir, QUrl):
                redir_url = redir
            else:
                redir_url = QUrl(str(redir))
            if (not redir_url.isValid()) or (redir_url.scheme() == ''):
                # Resolve relative to the original reply URL
                redir_url = reply.url().resolved(redir_url)
            req = QNetworkRequest(redir_url)
            req.setRawHeader(b'User-Agent', b'QGIS-Earthdata-DEM/1.0')
            if self.net.authcfg:
                try:
                    QgsApplication.authManager().updateNetworkRequest(req, self.net.authcfg)
                except Exception:
                    pass
            try:
                req.setAttribute(QNetworkRequest.FollowRedirectsAttribute, True)
            except Exception:
                pass
            r2 = self.net.nam.get(req)
            r2.downloadProgress.connect(self._on_progress)
            r2.finished.connect(lambda r=r2: self._on_reply_finished(r))
            reply.deleteLater()
            return

        if reply.error() != QNetworkReply.NoError:
            msg = reply.errorString()
            self._append_log(f'❌ Lỗi khi tải: {msg}')
            reply.deleteLater()
            # Stop whole flow on first error (đơn giản). Có thể đổi thành bỏ qua tile.
            self._reset_ui_error(msg)
            return

        # Write file
        try:
            data = bytes(reply.readAll())
            with open(self._current_target, 'wb') as f:
                f.write(data)
            self._downloaded_files.append(self._current_target)
            self._append_log(f'Đã tải: {os.path.basename(self._current_target)}')
        except Exception as e:
            msg = f'Lỗi ghi tệp: {e}'
            self._append_log(f'❌ {msg}')
            reply.deleteLater()
            self._reset_ui_error(msg)
            return

        reply.deleteLater()
        self._kick_next()

    # ------------------------------ After downloads ------------------------------ #
    def _after_all_downloads(self):
        # Expand archives (.zip) -> collect actual rasters (.hgt/.tif)
        try:
            expanded: List[str] = []
            for p in self._downloaded_files:
                if p.lower().endswith('.zip'):
                    try:
                        with zipfile.ZipFile(p) as z:
                            z.extractall(self._out_folder)
                            for n in z.namelist():
                                if n.lower().endswith(('.hgt', '.tif', '.tiff')):
                                    expanded.append(os.path.join(self._out_folder, n))
                    except Exception as ez:
                        self._reset_ui_error(f'Lỗi giải nén {os.path.basename(p)}: {ez}')
                        return
                else:
                    expanded.append(p)
            # Replace list with expanded files
            self._downloaded_files = expanded
        except Exception as e:
            self._reset_ui_error(f'Lỗi xử lý đầu vào: {e}')
            return

        # Determine user choices
        target_crs = self._target_crs()
        dtype_name = self.cboDtype.currentData()  # 'Int16', 'Float32', 'USE_INPUT', ...

        # 1) Mosaic
        mosaic_path = None
        try:
            if self.do_mosaic:
                self._append_log('Ghép mosaic (GDAL)…')
                self.prg.setValue(75)
                mosaic_path = self._abs_in_outfolder(self.mosaic_path, 'mosaic.tif')
                params = {
                    'INPUT': self._downloaded_files,
                    'PCT': False,
                    'SEPARATE': False,
                    'NODATA_INPUT': -32768,
                    'NODATA_OUTPUT': -32768,
                    'OPTIONS': 'COMPRESS=LZW',
                    'DATA_TYPE': 3,  # giữ dtype đầu vào
                    'OUTPUT': mosaic_path
                }
                fb = QgsProcessingFeedback()
                from qgis import processing
                processing.run('gdal:merge', params, feedback=fb)
                self._append_log(f'Đã tạo mosaic: {os.path.abspath(mosaic_path)}')

                # Reproject và/hoặc đổi dtype nếu cần
                need_reproj = target_crs is not None
                need_dtype = (dtype_name != 'USE_INPUT')
                if need_reproj or need_dtype:
                    self._append_log(f'Áp dụng {"reproject" if need_reproj else ""}{" & " if need_reproj and need_dtype else ""}{"dtype "+dtype_name if need_dtype else ""}…')
                    out_path = self._abs_in_outfolder('mosaic_final.tif', 'mosaic_final.tif')
                    from qgis import processing
                    if need_reproj:
                        params_warp = {
                            'INPUT': mosaic_path,
                            'SOURCE_CRS': None,
                            'TARGET_CRS': target_crs,
                            'RESAMPLING': 1,        # Bilinear cho DEM
                            'NODATA': -32768,
                            'MULTITHREADING': True,
                            'EXTRA': (f'-ot {dtype_name}' if need_dtype else ''),
                            'OUTPUT': out_path
                        }
                        try:
                            processing.run('gdal:warpreproject', params_warp, feedback=QgsProcessingFeedback())
                        except Exception:
                            processing.run('gdal:warp', params_warp, feedback=QgsProcessingFeedback())
                    else:
                        params_tr = {
                            'INPUT': mosaic_path,
                            'TARGET_CRS': None,
                            'NODATA': -32768,
                            'COPY_SUBDATASETS': False,
                            'OPTIONS': 'COMPRESS=LZW',
                            'EXTRA': f'-ot {dtype_name}',
                            'OUTPUT': out_path
                        }
                        processing.run('gdal:translate', params_tr, feedback=QgsProcessingFeedback())
                    mosaic_path = out_path
                self.prg.setValue(88)
        except Exception as e:
            self._reset_ui_error(f'Lỗi khi mosaic: {e}')
            return

        # 2) Clip
        clip_path = None
        try:
            if self.do_clip:
                from qgis import processing
                in_ras = mosaic_path if mosaic_path else self._downloaded_files
                if isinstance(in_ras, list):
                    if len(in_ras) > 1:
                        raise QgsProcessingException('Vui lòng bật Mosaic khi cắt nhiều tile.')
                    in_ras = in_ras[0]
                dtype_extra = (f'-ot {dtype_name}' if dtype_name != 'USE_INPUT' else '')
                # Nếu có AOI -> mask; nếu không -> dùng extent khung nhìn
                aoi_id = self.cboClipAoi.currentData()
                aoi_layer = _layer_by_id_or_name(aoi_id) if aoi_id else None
                if aoi_layer:
                    self._append_log('Cắt theo ranh giới (GDAL)…')
                    self.prg.setValue(93)
                    clip_path = self._abs_in_outfolder('clip.tif', 'clip.tif')
                    params = {
                        'INPUT': in_ras,
                        'MASK': aoi_layer,
                        'SOURCE_CRS': None,
                        'TARGET_CRS': (target_crs if target_crs is not None else None),
                        'CROP_TO_CUTLINE': True,
                        'KEEP_RESOLUTION': True,
                        'SET_RESOLUTION': False,
                        'X_RESOLUTION': None,
                        'Y_RESOLUTION': None,
                        'NODATA': -32768,
                        'ALPHA_BAND': False,
                        'DATA_TYPE': 3,
                        'EXTRA': (dtype_extra + ' -overwrite').strip(),
                        'OUTPUT': clip_path
                    }
                    processing.run('gdal:cliprasterbymasklayer', params, feedback=QgsProcessingFeedback())
                else:
                    # --- MỚI: Clip theo Extent khung nhìn ---
                    self._append_log('Cắt theo Extent khung nhìn (GDAL)…')
                    self.prg.setValue(93)
                    # Lấy extent hiện tại và đổi sang CRS đầu ra (nếu có)
                    canvas = iface.mapCanvas()
                    ext_src = canvas.extent()
                    crs_src = canvas.mapSettings().destinationCrs()
                    if target_crs and target_crs.isValid() and crs_src.isValid() and target_crs != crs_src:
                        ct = QgsCoordinateTransform(crs_src, target_crs, QgsProject.instance())
                        ext_dst = ct.transform(ext_src)
                    else:
                        ext_dst = ext_src
                    clip_path = self._abs_in_outfolder('clip.tif', 'clip.tif')
                    processing.run('gdal:cliprasterbyextent', {
                        'INPUT': in_ras,
                        'PROJWIN': ext_dst,
                        'NODATA': -32768,
                        'OPTIONS': 'COMPRESS=LZW',
                        'DATA_TYPE': 3,
                        'EXTRA': dtype_extra,
                        'OUTPUT': clip_path
                    }, feedback=QgsProcessingFeedback())
                self._append_log(f'Đã tạo raster cắt: {os.path.abspath(clip_path)}')
                self.prg.setValue(98)
        except Exception as e:
            self._reset_ui_error(f'Lỗi khi cắt: {e}')
            return

        # 3) Add to project
        to_add = []
        if clip_path and os.path.isfile(clip_path):
            to_add.append(clip_path)
        elif mosaic_path and os.path.isfile(mosaic_path):
            to_add.append(mosaic_path)
        else:
            to_add.extend([f for f in self._downloaded_files if os.path.isfile(f) and f.lower().endswith(('.tif', '.tiff', '.hgt'))])
        for p in to_add:
            rl = QgsRasterLayer(p, os.path.basename(p))
            if rl.isValid():
                QgsProject.instance().addMapLayer(rl)

        self.prg.setValue(100)
        self._append_log('✅ Hoàn tất.')
        self._reset_ui_ok()

    # ------------------------------ Settings ------------------------------ #
    def _load_settings(self):
        s = QSettings()
        self.cboDataset.setCurrentText(s.value('earthdata_dem/dataset', 'NASADEM_HGT'))
        self.txtFolder.setText(s.value('earthdata_dem/out_folder', ''))
        self.chkMosaic.setChecked(s.value('earthdata_dem/do_mosaic', False, type=bool))
        self.txtMosaic.setText(s.value('earthdata_dem/mosaic_path', ''))
        self.chkClip.setChecked(s.value('earthdata_dem/do_clip', False, type=bool))
        self.optAuthBasic.setChecked(s.value('earthdata_dem/auth_basic', True, type=bool))
        self.optAuthCfg.setChecked(not self.optAuthBasic.isChecked())
        self.txtUser.setText(s.value('earthdata_dem/user', ''))
        if s.value('earthdata_dem/remember', False, type=bool):
            self.txtPass.setText(s.value('earthdata_dem/pass', ''))
            self.chkRemember.setChecked(True)
        # CRS settings
        self.chkSetOutCrs.setChecked(s.value('earthdata_dem/set_out_crs', False, type=bool))
        out_auth = s.value('earthdata_dem/out_crs_authid', '')
        if out_auth:
            crs = QgsCoordinateReferenceSystem(out_auth)
            if HAS_PROJ_SELECTOR and getattr(self, 'projSel', None) and crs.isValid():
                self.projSel.setCrs(crs)
            elif hasattr(self, 'txtOutCrs'):
                self.txtOutCrs.setText(out_auth)
        # dtype
        saved_ot = s.value('earthdata_dem/out_dtype', 'Int16')
        idx = max(0, self.cboDtype.findData(saved_ot))
        self.cboDtype.setCurrentIndex(idx)

    def _save_settings(self):
        s = QSettings()
        s.setValue('earthdata_dem/dataset', self.cboDataset.currentText())
        s.setValue('earthdata_dem/out_folder', self.txtFolder.text())
        s.setValue('earthdata_dem/do_mosaic', self.chkMosaic.isChecked())
        s.setValue('earthdata_dem/mosaic_path', self.txtMosaic.text())
        s.setValue('earthdata_dem/do_clip', self.chkClip.isChecked())
        s.setValue('earthdata_dem/auth_basic', self.optAuthBasic.isChecked())
        s.setValue('earthdata_dem/user', self.txtUser.text())
        s.setValue('earthdata_dem/remember', self.chkRemember.isChecked())
        # CRS settings
        s.setValue('earthdata_dem/set_out_crs', self.chkSetOutCrs.isChecked())
        if HAS_PROJ_SELECTOR and getattr(self, 'projSel', None):
            s.setValue('earthdata_dem/out_crs_authid', self.projSel.crs().authid() if self.projSel.crs().isValid() else '')
        elif hasattr(self, 'txtOutCrs'):
            s.setValue('earthdata_dem/out_crs_authid', self.txtOutCrs.text().strip())
        # dtype
        s.setValue('earthdata_dem/out_dtype', self.cboDtype.currentData())
        if self.chkRemember.isChecked():
            s.setValue('earthdata_dem/pass', self.txtPass.text())
        else:
            try:
                s.remove('earthdata_dem/pass')
            except Exception:
                pass

    # ------------------------------ UI reset helpers ------------------------------ #
    def _reset_ui_ok(self):
        self.btnRun.setEnabled(True)
        self.btnSearch.setEnabled(True)
        self.btnCancel.setEnabled(False)

    def _reset_ui_error(self, msg: str):
        QtWidgets.QMessageBox.critical(self, 'Lỗi', msg)
        self._reset_ui_ok()

    def _cancel(self):
        # NAM không có cancel tất cả theo batch; có thể tạm bỏ qua hoặc giữ nguyên vì tải tuần tự
        self._append_log('Đã yêu cầu hủy. Bạn có thể đóng dock.')
        self._reset_ui_ok()


# ------------------------------ Minimal plugin hook (optional) ------------------------------ #
class EarthdataDemPlugin:
    """Minimal harness to toggle the dock (if you want to wire directly)."""
    def __init__(self):
        self.dock: Optional[EarthdataDemDock] = None

    def toggle(self):
        if not self.dock:
            self.dock = EarthdataDemDock(iface.mainWindow())
            iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.setVisible(True)
        self.dock.raise_()
