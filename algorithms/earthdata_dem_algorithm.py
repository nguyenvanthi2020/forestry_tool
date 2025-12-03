# -*- coding: utf-8 -*-
"""
Processing: Earthdata DEM Downloader (QNetworkAccessManager + QGIS AuthConfig)
- Tải DEM từ NASA CMR + Earthdata (Auth qua QGIS Auth Config hoặc Username/Password – fallback)
- Mosaic (BuildVRT -> Translate/Warp), NoData=-32768, giữ đúng dtype (mặc định Int16)
- Clip theo AOI (nếu có) hoặc Extent (khi bật Clip mà không chọn layer)
- CRS đầu ra (tùy chọn), Kiểu dữ liệu đầu ra (Byte/UInt16/Int16/Int32/Float32/Float64/Keep)
- Warm-up đăng nhập cho URS/LPDAAC/CMR (danh sách host mặc định, ẩn khỏi UI)
"""

from __future__ import annotations
import os, re, json, zipfile
from typing import List, Optional, Tuple

from qgis.PyQt.QtCore import QObject, QEventLoop, QUrl
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply, QAuthenticator

from qgis.core import (
    QgsApplication, QgsProject, QgsGeometry, QgsRectangle,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsWkbTypes,
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterBoolean,
    QgsProcessingParameterExtent, QgsProcessingParameterFolderDestination,
    QgsProcessingParameterRasterDestination, QgsProcessingParameterCrs,
    QgsProcessingParameterString, QgsProcessingParameterAuthConfig,
    QgsProcessingContext, QgsProcessingFeedback, QgsProcessingException,
    QgsFeatureRequest, QgsRasterLayer
)
from qgis import processing


# ---------- helpers: network ----------

class _Net(QObject):
    """
    QNetworkAccessManager wrapper với điều khiển 'apply_auth' theo từng request.
    - apply_auth=False: không chèn AuthConfig và không phản hồi Basic 401.
    - apply_auth=True : dùng AuthConfig (hoặc user/pass fallback).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        self.user: Optional[str] = None
        self.passwd: Optional[str] = None
        self.authcfg: Optional[str] = None  # QGIS Auth Config ID
        self._allow_auth: bool = True       # bật/tắt xử lý 401 cho lần gọi hiện tại
        self.nam.authenticationRequired.connect(self._on_auth_required)

    def _on_auth_required(self, reply: QNetworkReply, authenticator: QAuthenticator):
        if not self._allow_auth:
            return
        try:
            if self.authcfg:
                QgsApplication.authManager().updateNetworkReply(reply, authenticator, self.authcfg)
                return
        except Exception:
            pass
        if self.user and self.passwd:
            authenticator.setUser(self.user)
            authenticator.setPassword(self.passwd)

    def _prepare_req(self, qurl: QUrl, apply_auth: bool) -> QNetworkRequest:
        req = QNetworkRequest(qurl)
        req.setRawHeader(b'User-Agent', b'QGIS-Earthdata-DEM/1.0')
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

    def _get_block(self, req: QNetworkRequest, timeout_ms: int = 300000, allow_auth: bool = True) -> QNetworkReply:
        prev = self._allow_auth
        self._allow_auth = allow_auth
        try:
            rep = self.nam.get(req)
            loop = QEventLoop()
            rep.finished.connect(loop.quit)
            from qgis.PyQt.QtCore import QTimer
            t = QTimer(); t.setSingleShot(True)
            t.timeout.connect(loop.quit)
            t.start(timeout_ms)
            loop.exec_()
            t.stop()
            return rep
        finally:
            self._allow_auth = prev

    def get_follow_redirects(self, url: str, timeout_ms: int = 300000, apply_auth: bool = True) -> QNetworkReply:
        max_hops = 10
        qurl = QUrl(url)
        while max_hops > 0:
            req = self._prepare_req(qurl, apply_auth=apply_auth)
            rep = self._get_block(req, timeout_ms=timeout_ms, allow_auth=apply_auth)
            redir = rep.attribute(QNetworkRequest.RedirectionTargetAttribute)
            if redir is not None:
                rep.deleteLater()
                qurl = redir if isinstance(redir, QUrl) else QUrl(str(redir))
                if (not qurl.isValid()) or (qurl.scheme() == ''):
                    qurl = req.url().resolved(qurl)
                max_hops -= 1
                continue
            return rep
        return rep

    def warm_up(self, hosts: List[str], timeout_ms: int = 120000):
        """
        Kích hoạt vòng xác thực trước khi gọi CMR/tải file.
        Với URS, truy cập /oauth/authorize?... để buộc 401 Basic và điền từ AuthConfig.
        """
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


# ---------- helpers: geometry ----------

def _to4326_rect(rect: QgsRectangle, src_crs: QgsCoordinateReferenceSystem) -> QgsRectangle:
    if not src_crs or not src_crs.isValid() or src_crs.authid() == 'EPSG:4326':
        return rect
    ct = QgsCoordinateTransform(src_crs, QgsCoordinateReferenceSystem('EPSG:4326'), QgsProject.instance())
    return ct.transform(rect)

def _get_union_polygon_geom(src, only_selected: bool) -> Optional[QgsGeometry]:
    it = src.getFeatures(QgsFeatureRequest().setSubsetOfAttributes([]))
    geoms: List[QgsGeometry] = []
    for f in it:
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

def _bbox_of_geom4326(geom, geom_crs: QgsCoordinateReferenceSystem) -> Tuple[float, float, float, float]:
    rect = geom.boundingBox()
    rect4326 = _to4326_rect(rect, geom_crs)
    return (rect4326.xMinimum(), rect4326.yMinimum(), rect4326.xMaximum(), rect4326.yMaximum())


# ---------- helpers: registry ----------

def _algo_exists(aid: str) -> bool:
    try:
        return QgsApplication.processingRegistry().algorithmById(aid) is not None
    except Exception:
        return False


# ---------- Algorithm ----------

class EarthdataDemAlgorithm(QgsProcessingAlgorithm):
    P_DATASET = 'DATASET'
    P_AOI = 'AOI'
    P_USE_SELECTED = 'USE_SELECTED'
    P_EXTENT = 'EXTENT'
    P_OUTFOLDER = 'OUT_FOLDER'
    P_DO_MOSAIC = 'DO_MOSAIC'
    P_MOSAIC = 'MOSAIC'
    P_DO_CLIP = 'DO_CLIP'
    P_CLIP_AOI = 'CLIP_AOI'
    P_CLIP = 'CLIP'
    P_OUT_CRS = 'OUT_CRS'
    P_DTYPE = 'DTYPE'
    P_USERNAME = 'USERNAME'
    P_PASSWORD = 'PASSWORD'
    P_AUTHCFG = 'AUTHCFG'

    _DATASETS = ['NASADEM_HGT', 'SRTMGL1', 'ASTGTM']
    _DTYPE_LABELS = ['Int16 (default)', 'Byte', 'UInt16', 'Int32', 'Float32', 'Float64', 'Keep input']
    _DTYPE_MAP = ['Int16', 'Byte', 'UInt16', 'Int32', 'Float32', 'Float64', 'USE_INPUT']

    # Danh sách host WARM-UP (ẩn khỏi UI)
    _DEFAULT_AUTH_HOSTS = [
        'https://urs.earthdata.nasa.gov',
        'https://data.lpdaac.earthdatacloud.nasa.gov',
        'https://cmr.earthdata.nasa.gov'
    ]

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(self.P_DATASET, 'Loại DEM (Dataset)', options=self._DATASETS, defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource(self.P_AOI, 'Khu vực quan tâm - AOI (đa giác)', [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterBoolean(self.P_USE_SELECTED, 'Chỉ đối tượng được chọn (AOI)', defaultValue=False))
        self.addParameter(QgsProcessingParameterExtent(self.P_EXTENT, 'Theo khung bản đồ hiện tại (nếu không có AOI)', optional=True))
        self.addParameter(QgsProcessingParameterFolderDestination(self.P_OUTFOLDER, 'Thư mục tải về'))

        self.addParameter(QgsProcessingParameterBoolean(self.P_DO_MOSAIC, 'Ghép các mảnh', defaultValue=True))
        self.addParameter(QgsProcessingParameterRasterDestination(self.P_MOSAIC, 'Tên tệp ghép (GeoTIFF)', optional=True))

        self.addParameter(QgsProcessingParameterBoolean(self.P_DO_CLIP, 'Cắt theo ranh giới (hoặc theo khung bản đồ hiện tại)', defaultValue=False))
        self.addParameter(QgsProcessingParameterFeatureSource(self.P_CLIP_AOI, 'Vùng cắt (đa giác)', [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterRasterDestination(self.P_CLIP, 'Tên tệp cắt (GeoTIFF)', optional=True, createByDefault=False))

        self.addParameter(QgsProcessingParameterCrs(self.P_OUT_CRS, 'Hệ tọa độ đầu ra', optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.P_DTYPE, 'Kiểu dữ liệu đầu ra', options=self._DTYPE_LABELS, defaultValue=0))

        # Auth: ưu tiên Auth Config (UI che mật khẩu), fallback Username/Password
        self.addParameter(QgsProcessingParameterAuthConfig(self.P_AUTHCFG, 'Cấu hình xác thực của QGIS (khuyến cáo)', optional=True))
        self.addParameter(QgsProcessingParameterString(self.P_USERNAME, 'Tên đăng nhập Earthdata', optional=True))
        self.addParameter(QgsProcessingParameterString(self.P_PASSWORD, 'Mật khẩu đăng nhập Earthdata', optional=True))

        # Không còn tham số 'AUTH_HOSTS' trên UI (ẩn)

    def name(self): return 'earthdata_dem_downloader'
    def displayName(self): return 'Tải DEM từ Earthdata (NASA)'
    def group(self): return 'Tiện ích Raster'
    def groupId(self): return 'raster_utils'
    def createInstance(self): return EarthdataDemAlgorithm()
    def shortHelpString(self):
        return ("Tải DEM từ Earthdata (NASA).\n"

            "• Loại DEM (Dataset): chọn lại dữ liệu DEM (NASADEM_HGT/SRTMGL1/ASTGTM) cần tải về.\n"
            "• Khu vực quan tâm - AOI (đa giác): nếu bỏ trống sẽ dùng EXTENT. (Thuật toán hợp tất cả đa giác.)\n"
            "• Chỉ đối tượng được chọn (AOI): Nếu chọn thì thuật toán sẽ tính toán vùng tải theo các đôi tượng được chon.\n"
            "• Theo khung bản đồ hiện tại (nếu không có AOI): dùng khi không có AOI; cũng dùng làm phạm vi cắt nếu bật CLIP mà không chọn lớp để cắt.\n"
            "• Thư mục tải về: thư mục lưu trữ kết quả tải về/giải nén/kết quả khác.\n"
            "• Ghép các mảnh: ghép các mảnh tải về và xuất ra định dạng GeoTIFF.\n" 
            "• Tên tệp ghép (GeoTIFF): Tùy chọn - chọn đường dẫn GeoTIFF đầu ra (nếu trống sẽ mặc định trong Thư mục tải về).\n"
            "• Cắt theo ranh giới (hoặc theo khung bản đồ hiện tại): cắt kết quả đã ghép các mảnh theo ranh giới đã chọn hoặc theo khung bản đồ hiện tại.\n"
            "• Vùng cắt (đa giác): lớp bản đồ dạng vùng để cắt (tùy chọn); nếu trống sẽ cắt theo khung bản đồ hiện tại mà bạn đã chọn để tải dữ liệu.\n"
            "• Tên tệp cắt (GeoTIFF): đường dẫn kết quả cắt.\n"
            "<B>Lưu ý:<B> muốn cắt nhiều tile thì nên bật Mosaic.\n"
            "• Hệ tọa độ đầu ra: Hệ tọa độ (CRS) đầu ra (nếu bật thì thuật toán sẽ nắn theo hệ tọa độ đã chọn trước khi ghi).\n"
            "• Kiểu dữ liệu đầu ra: kiểu dữ liệu đầu ra (mặc định Int16, có tùy chọn “giữ theo đầu vào”).\n"
            "• Xác thực (khuyến cáo): Cấu hình xác thực QGIS chứa thông tin đăng nhập Earthdata; nếu không có, dùng Tên/Mật khẩu.\n"

            "<B>Yêu cầu:<B> Tài khoản Earthdata đã “Approve/Accept” điều khoản dataset tại Earthdata Login → Applications.\n")

    # ---- internals ----

    def _dtype_from_idx(self, idx: int) -> str:
        if 0 <= idx < len(self._DTYPE_MAP):
            return self._DTYPE_MAP[idx]
        return 'Int16'

    def _cmr_search(self, net: _Net, short_name: str, bbox: Tuple[float, float, float, float], fb: QgsProcessingFeedback) -> List[str]:
        west, south, east, north = bbox
        url = ('https://cmr.earthdata.nasa.gov/search/granules.json?'
               f'short_name={short_name}&bounding_box={west},{south},{east},{north}&page_size=2000&sort_key=start_date')
        # CMR là public: apply_auth=False để tránh 401 không cần thiết
        rep = net.get_follow_redirects(url, timeout_ms=120000, apply_auth=False)
        if rep.error() != QNetworkReply.NoError:
            raise QgsProcessingException(f'{rep.errorString()} (host: {rep.url().host()})')
        data = bytes(rep.readAll()); rep.deleteLater()
        try:
            j = json.loads(data.decode('utf-8'))
        except Exception as e:
            raise QgsProcessingException(f'CMR JSON parse error: {e}')
        entries = (j.get('feed', {}) or {}).get('entry', []) or []
        urls: List[str] = []
        for it in entries:
            for l in it.get('links', []) or []:
                href = l.get('href'); rel = l.get('rel', '')
                if not href: continue
                if href.startswith('s3://'): continue
                if ('data#' in rel) or rel.endswith('/data') or href.lower().endswith(('.zip', '.hgt', '.tif', '.tiff')):
                    urls.append(href)
        urls = [u for u in urls if u.lower().endswith(('.hgt', '.tif', '.tiff', '.zip'))]
        seen=set(); uniq=[]
        for u in urls:
            if u not in seen: uniq.append(u); seen.add(u)
        return uniq

    def _download_file(self, net: _Net, url: str, target_path: str, fb: QgsProcessingFeedback):
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        # Endpoint tải là protected: apply_auth=True
        rep = net.get_follow_redirects(url, timeout_ms=300000, apply_auth=True)
        if rep.error() != QNetworkReply.NoError:
            err = f'{rep.errorString()} (host: {rep.url().host()})'
            rep.deleteLater()
            raise QgsProcessingException(f'Tải về bị lỗi: {err}')
        try:
            data = bytes(rep.readAll())
            with open(target_path, 'wb') as f: f.write(data)
        finally:
            rep.deleteLater()

    def _extent_to_bbox4326(self, extent: QgsRectangle, src_crs: QgsCoordinateReferenceSystem) -> Tuple[float, float, float, float]:
        rect4326 = _to4326_rect(extent, src_crs)
        return (rect4326.xMinimum(), rect4326.yMinimum(), rect4326.xMaximum(), rect4326.yMaximum())

    # ---- main ----

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback: QgsProcessingFeedback):
        dataset = self._DATASETS[self.parameterAsEnum(parameters, self.P_DATASET, context)]
        aoi_src = self.parameterAsSource(parameters, self.P_AOI, context)
        use_sel = self.parameterAsBoolean(parameters, self.P_USE_SELECTED, context)
        extent_param = self.parameterAsExtent(parameters, self.P_EXTENT, context)

        out_folder = self.parameterAsString(parameters, self.P_OUTFOLDER, context).strip()
        if not out_folder: raise QgsProcessingException('Không có thư mục đầu ra.')

        do_mosaic = self.parameterAsBoolean(parameters, self.P_DO_MOSAIC, context)
        mosaic_dest = self.parameterAsOutputLayer(parameters, self.P_MOSAIC, context)

        do_clip = self.parameterAsBoolean(parameters, self.P_DO_CLIP, context)
        clip_src = self.parameterAsSource(parameters, self.P_CLIP_AOI, context)
        clip_dest = self.parameterAsOutputLayer(parameters, self.P_CLIP, context)

        out_crs = self.parameterAsCrs(parameters, self.P_OUT_CRS, context)
        if out_crs and not out_crs.isValid(): out_crs = None

        dtype_idx = self.parameterAsEnum(parameters, self.P_DTYPE, context)
        dtype_name = self._dtype_from_idx(dtype_idx)

        authcfg = self.parameterAsString(parameters, self.P_AUTHCFG, context).strip() or None
        user = self.parameterAsString(parameters, self.P_USERNAME, context).strip() or None
        passwd = self.parameterAsString(parameters, self.P_PASSWORD, context) or None

        # bbox cho tìm CMR
        if aoi_src:
            geom = _get_union_polygon_geom(aoi_src, use_sel)
            if geom is None: raise QgsProcessingException('Vùng quan tâm không có đối tượng vùng hợp lệ.')
            bbox4326 = _bbox_of_geom4326(geom, aoi_src.sourceCrs())
        elif extent_param and not extent_param.isEmpty():
            proj_crs = context.project().crs() if context.project() else QgsProject.instance().crs()
            bbox4326 = self._extent_to_bbox4326(extent_param, proj_crs)
        else:
            raise QgsProcessingException('Hãy cung cấp vùng quan tâm hoặc khung bản đồ hiện tại.')

        feedback.pushInfo('Vùng quan tâm (W,S,E,N): %.6f, %.6f, %.6f, %.6f' % bbox4326)

        # Khởi tạo net + warm-up (ẩn hosts)
        net = _Net()
        net.authcfg = authcfg
        if not authcfg:
            net.user = user
            net.passwd = passwd

        if self._DEFAULT_AUTH_HOSTS:
            #feedback.pushInfo('Warm-up auth on: ' + ', '.join(self._DEFAULT_AUTH_HOSTS))
            net.warm_up(self._DEFAULT_AUTH_HOSTS, timeout_ms=120000)

        # --- Search CMR (NO AUTH) ---
        urls = self._cmr_search(net, dataset, bbox4326, feedback)
        if not urls:
            feedback.reportError('Không có mảnh nào.')
            return {}
        feedback.pushInfo('Đã tìm thấy %d mảnh.' % len(urls))
        for u in urls[:20]: feedback.pushInfo('• ' + os.path.basename(u))
        if len(urls) > 20: feedback.pushInfo('  ...')

        # --- Download (WITH AUTH) ---
        os.makedirs(out_folder, exist_ok=True)
        downloaded: List[str] = []
        for i, u in enumerate(urls, start=1):
            if feedback.isCanceled(): break
            base = os.path.basename(u.split('?')[0])
            tgt = os.path.join(out_folder, base)
            feedback.pushInfo('Đang tải (%d/%d): %s' % (i, len(urls), base))
            self._download_file(net, u, tgt, feedback)
            downloaded.append(tgt)
            feedback.setProgress(int(i / max(1, len(urls)) * 60.0))

        # --- Expand ZIPs ---
        expanded: List[str] = []
        for p in downloaded:
            if p.lower().endswith('.zip'):
                with zipfile.ZipFile(p) as z:
                    z.extractall(out_folder)
                    for n in z.namelist():
                        if n.lower().endswith(('.hgt', '.tif', '.tiff')):
                            expanded.append(os.path.join(out_folder, n))
            else:
                expanded.append(p)
        downloaded = expanded

        # --- Mosaic ---
        mosaic_path = ''
        if do_mosaic:
            mosaic_path = mosaic_dest if mosaic_dest else os.path.join(out_folder, 'mosaic.tif')
            vrt_path = os.path.join(out_folder, 'mosaic.vrt')
            need_reproj = bool(out_crs and out_crs.isValid())
            need_dtype  = (dtype_name != 'USE_INPUT')

            if _algo_exists('gdal:buildvirtualraster'):
                feedback.pushInfo('Đang tạo VRT (gdal:buildvirtualraster)...')
                processing.run('gdal:buildvirtualraster', {
                    'INPUT': downloaded,
                    'RESOLUTION': 0, 'SEPARATE': False,
                    'PROJ_DIFFERENCE': True, 'ADD_ALPHA': False,
                    'ASSIGN_CRS': None, 'RESAMPLING': 0,
                    'SRC_NODATA': '-32768',
                    'EXTRA': '', 'OUTPUT': vrt_path
                }, context=context, feedback=feedback)

                if not need_reproj:
                    feedback.pushInfo('Đang ghép dữ liệu GeoTIFF...')
                    processing.run('gdal:translate', {
                        'INPUT': vrt_path,
                        'TARGET_CRS': None,
                        'NODATA': -32768,
                        'COPY_SUBDATASETS': False,
                        'OPTIONS': '',  # tránh cảnh báo: -co tách ở EXTRA
                        'EXTRA': ((
                            ('-ot %s ' % dtype_name) if need_dtype else ''
                        ) + '-unscale -co COMPRESS=LZW -co TILED=YES').strip(),
                        'DATA_TYPE': 0,
                        'OUTPUT': mosaic_path
                    }, context=context, feedback=feedback)
                else:
                    feedback.pushInfo('Đang nắn hệ tọa độ và ghi dữ liệu GeoTIFF...')
                    warp_id = 'gdal:warpreproject' if _algo_exists('gdal:warpreproject') else 'gdal:warp'
                    processing.run(warp_id, {
                        'INPUT': vrt_path,
                        'SOURCE_CRS': None,
                        'TARGET_CRS': out_crs,
                        'RESAMPLING': 1,  # bilinear
                        'NODATA': -32768,
                        'MULTITHREADING': True,
                        'EXTRA': ((('-ot %s ' % dtype_name) if need_dtype else '') +
                                  '-co COMPRESS=LZW -co TILED=YES').strip(),
                        'OUTPUT': mosaic_path
                    }, context=context, feedback=feedback)
            else:
                feedback.pushInfo('gdal:buildvirtualraster không tìm thấy — quay lại gdal:merge...')
                tmp_mos = os.path.join(out_folder, 'mosaic_tmp.tif')
                processing.run('gdal:merge', {
                    'INPUT': downloaded,
                    'PCT': False, 'SEPARATE': False,
                    'NODATA_INPUT': -32768, 'NODATA_OUTPUT': -32768,
                    'OPTIONS': '',  # tránh cảnh báo
                    'EXTRA': '-co COMPRESS=LZW -co TILED=YES',
                    'DATA_TYPE': 0,
                    'OUTPUT': tmp_mos
                }, context=context, feedback=feedback)

                if need_reproj:
                    warp_id = 'gdal:warpreproject' if _algo_exists('gdal:warpreproject') else 'gdal:warp'
                    processing.run(warp_id, {
                        'INPUT': tmp_mos,
                        'SOURCE_CRS': None,
                        'TARGET_CRS': out_crs,
                        'RESAMPLING': 1,
                        'NODATA': -32768,
                        'MULTITHREADING': True,
                        'EXTRA': ((('-ot %s ' % dtype_name) if need_dtype else '') +
                                  '-co COMPRESS=LZW -co TILED=YES').strip(),
                        'OUTPUT': mosaic_path
                    }, context=context, feedback=feedback)
                else:
                    processing.run('gdal:translate', {
                        'INPUT': tmp_mos,
                        'TARGET_CRS': None,
                        'NODATA': -32768,
                        'COPY_SUBDATASETS': False,
                        'OPTIONS': '',  # tránh cảnh báo
                        'EXTRA': ((
                            ('-ot %s ' % dtype_name) if need_dtype else ''
                        ) + '-unscale -co COMPRESS=LZW -co TILED=YES').strip(),
                        'DATA_TYPE': 0,
                        'OUTPUT': mosaic_path
                    }, context=context, feedback=feedback)

            feedback.setProgress(80)
        else:
            feedback.pushInfo('Dừng ghép.')

        # --- Clip (optional) — by mask nếu có, nếu không thì by extent ---
        clip_path = ''
        if do_clip:
            in_ras = mosaic_path if mosaic_path else downloaded
            if isinstance(in_ras, list):
                if len(in_ras) > 1:
                    raise QgsProcessingException('Có nhiều mảnh cần cắt. Hãy bật chế độ ghép.')
                in_ras = in_ras[0]
            clip_path = clip_dest if clip_dest else os.path.join(out_folder, 'clip.tif')

            if clip_src is not None:
                feedback.pushInfo('Đang cắt theo ranh giới (GDAL)...')
                processing.run('gdal:cliprasterbymasklayer', {
                    'INPUT': in_ras,
                    'MASK': clip_src,
                    'SOURCE_CRS': None,
                    'TARGET_CRS': (out_crs if (out_crs and out_crs.isValid()) else None),
                    'CROP_TO_CUTLINE': True,
                    'KEEP_RESOLUTION': True,
                    'SET_RESOLUTION': False,
                    'X_RESOLUTION': None,
                    'Y_RESOLUTION': None,
                    'NODATA': -32768,
                    'ALPHA_BAND': False,
                    'DATA_TYPE': 0,
                    'EXTRA': ((('-ot %s ' % dtype_name) if (dtype_name != 'USE_INPUT') else '') +
                              '-co COMPRESS=LZW -co TILED=YES -overwrite').strip(),
                    'OUTPUT': clip_path
                }, context=context, feedback=feedback)
            else:
                feedback.pushInfo('Đang cắt theo khung bản đồ hiện tại bạn đã chọn (GDAL)...')
                if extent_param and not extent_param.isEmpty():
                    src_crs = context.project().crs() if context.project() else QgsProject.instance().crs()
                    dst_crs = out_crs if (out_crs and out_crs.isValid()) else src_crs
                    if dst_crs and dst_crs.isValid() and src_crs.isValid() and src_crs != dst_crs:
                        ct = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
                        ext_dst = ct.transform(extent_param)
                    else:
                        ext_dst = extent_param
                else:
                    rect4326 = QgsRectangle(bbox4326[0], bbox4326[1], bbox4326[2], bbox4326[3])
                    if out_crs and out_crs.isValid() and out_crs.authid() != 'EPSG:4326':
                        ct = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'), out_crs, QgsProject.instance())
                        ext_dst = ct.transform(rect4326)
                    else:
                        ext_dst = rect4326

                # gdal:cliprasterbyextent là wrapper gdal_translate -projwin
                processing.run('gdal:cliprasterbyextent', {
                    'INPUT': in_ras,
                    'PROJWIN': ext_dst,
                    'NODATA': -32768,
                    'OPTIONS': '',  # tránh cảnh báo
                    'DATA_TYPE': 0,
                    'EXTRA': ((
                        ('-ot %s ' % dtype_name) if dtype_name != 'USE_INPUT' else ''
                    ) + '-co COMPRESS=LZW -co TILED=YES').strip(),
                    'OUTPUT': clip_path
                }, context=context, feedback=feedback)

            feedback.setProgress(95)
        else:
            feedback.pushInfo('Dừng cắt.')

        # --- Add to project (optional) ---
        try:
            add_paths = []
            if do_clip and clip_path and os.path.isfile(clip_path):
                add_paths.append(clip_path)
            elif mosaic_path and os.path.isfile(mosaic_path):
                add_paths.append(mosaic_path)
            for p in add_paths:
                rl = QgsRasterLayer(p, os.path.basename(p))
                if rl and rl.isValid():
                    QgsProject.instance().addMapLayer(rl)
        except Exception:
            pass

        # --- Results ---
        results = {}
        if mosaic_path and os.path.isfile(mosaic_path):
            results[self.P_MOSAIC] = mosaic_path
        if do_clip and clip_path and os.path.isfile(clip_path):
            results[self.P_CLIP] = clip_path
        return results
