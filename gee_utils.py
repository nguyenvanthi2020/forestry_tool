# -*- coding: utf-8 -*-
"""
Tiện ích dùng chung cho GEE DEM:
- Khởi tạo Earth Engine
- Chọn dataset
- AOI: QgsRectangle -> (ee.Geometry.Rectangle, LinearRing)
- Ước lượng số pixel cục bộ (không gọi server)
- Ngưỡng pixels cho tải trực tiếp
- Kiểm tra thư mục Google Drive (nếu có token) để tránh tạo thư mục trùng tên
"""
import os
import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
    QgsPointXY,
)

# ---------------- Earth Engine ----------------
_EE_READY = False
def ensure_ee_initialized():
    """Khởi tạo ee một lần (dùng thiết lập ee_plugin nếu có)."""
    global _EE_READY
    if _EE_READY:
        import ee
        return ee
    import ee
    try:
        ee.Initialize()
    except Exception:
        ee.Authenticate()
        ee.Initialize()
    _EE_READY = True
    return ee

# ---------------- DEM datasets ----------------
def _img_srtm():
    import ee
    return ee.Image("USGS/SRTMGL1_003")

def _img_nasadem():
    import ee
    return ee.Image("NASA/NASADEM_HGT/001").select("elevation")

def _img_cop_glo30():
    import ee
    return ee.Image("COPERNICUS/DEM/GLO30").select("DEM")

def _img_alos_aw3d30():
    import ee
    return ee.Image("JAXA/ALOS/AW3D30/V3_2").select("AVE_DSM")

DATASETS = {
    "SRTMGL1 (30m)": _img_srtm,
    "NASADEM (30m)": _img_nasadem,
    "COP GLO-30 (30m)": _img_cop_glo30,
    "ALOS AW3D30 (30m)": _img_alos_aw3d30,
}

def ee_dem_image(name_key: str):
    fn = DATASETS.get(name_key)
    if not fn and isinstance(name_key, int) and 0 <= name_key < len(DATASETS):
        fn = list(DATASETS.values())[name_key]
    if not fn:
        raise Exception(f"Dataset không hợp lệ: {name_key}")
    return fn()

# ---------------- AOI helpers ----------------
def _to_wgs84_rect(rect: QgsRectangle, src_crs: QgsCoordinateReferenceSystem):
    rect = QgsRectangle(rect)
    rect.normalize()
    if src_crs.authid() == "EPSG:4326":
        return rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    xform = QgsCoordinateTransform(src_crs, wgs84, QgsProject.instance())
    p1 = xform.transform(QgsPointXY(rect.xMinimum(), rect.yMinimum()))
    p2 = xform.transform(QgsPointXY(rect.xMaximum(), rect.yMaximum()))
    xmin = min(p1.x(), p2.x()); xmax = max(p1.x(), p2.x())
    ymin = min(p1.y(), p2.y()); ymax = max(p1.y(), p2.y())
    return xmin, ymin, xmax, ymax

def _ring_from_bounds(bounds):
    xmin, ymin, xmax, ymax = bounds
    return [[xmin, ymin], [xmin, ymax], [xmax, ymax], [xmax, ymin], [xmin, ymin]]

def qrect_to_ee_geometry(rect: QgsRectangle, src_crs: QgsCoordinateReferenceSystem):
    """Trả về (ee.Geometry.Rectangle, LinearRing)."""
    ee = ensure_ee_initialized()
    xmin, ymin, xmax, ymax = _to_wgs84_rect(rect, src_crs)
    ring = _ring_from_bounds((xmin, ymin, xmax, ymax))
    geom = ee.Geometry.Rectangle([xmin, ymin, xmax, ymax], proj="EPSG:4326", geodesic=False, maxError=1)
    return geom, ring

def polygon_from_ring(ring):
    """GeoJSON Polygon từ LinearRing."""
    return {"type": "Polygon", "coordinates": [ring]}

def make_tmp_export_region(aoi_tuple_or_ring):
    """Giữ tương thích: nhận tuple bounds(4) -> ring; nhận ring(list) -> trả nguyên."""
    if isinstance(aoi_tuple_or_ring, (tuple, list)) and len(aoi_tuple_or_ring) == 4:
        return _ring_from_bounds(aoi_tuple_or_ring)
    if isinstance(aoi_tuple_or_ring, list):
        return aoi_tuple_or_ring
    raise Exception("make_tmp_export_region: cần ring (list) hoặc bounds tuple 4 phần tử.")

# ---------------- Ước lượng số pixel (cục bộ) ----------------
def _meters_per_degree_lat(): return 110574.0
def _meters_per_degree_lon_at_lat(lat_deg): return 111320.0 * math.cos(math.radians(lat_deg))

def estimate_pixel_count(rect: QgsRectangle, src_crs: QgsCoordinateReferenceSystem, scale_m: int) -> float:
    xmin, ymin, xmax, ymax = _to_wgs84_rect(rect, src_crs)
    mid_lat = (ymin + ymax) / 2.0
    w_m = max((xmax - xmin) * max(_meters_per_degree_lon_at_lat(mid_lat), 1.0), 0)
    h_m = max((ymax - ymin) * _meters_per_degree_lat(), 0)
    return max(w_m * h_m / max(scale_m, 1) ** 2, 0.0)

# GEE thường giới hạn ~48–50MB request; ngưỡng px tham khảo cho direct:
DIRECT_DOWNLOAD_PIXEL_LIMIT = 14_000_000

# ---------------- Google Drive folder check (tùy chọn) ----------------
def _drive_paths():
    # ưu tiên extlibs của ee_plugin để có googleapiclient
    from qgis.core import QgsApplication
    base = QgsApplication.qgisSettingsDirPath()
    extlibs = os.path.join(base, "python", "plugins", "ee_plugin", "extlibs")
    plugin_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    token_path = os.path.join(plugin_dir, "drive_token.json")
    return extlibs, token_path

def try_resolve_existing_drive_folder_name(desired_name: str):
    """
    Trả 'desired_name' nếu xác định có đúng 1 thư mục tên đó.
    Trả None nếu không chắc chắn (thiếu libs/token hoặc có nhiều thư mục trùng tên).
    KHÔNG yêu cầu người dùng đăng nhập, KHÔNG tạo mới thư mục.
    """
    try:
        import sys as _sys
        extlibs, token_path = _drive_paths()
        if os.path.isdir(extlibs) and extlibs not in _sys.path:
            _sys.path.insert(0, extlibs)

        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]

        if not os.path.exists(token_path):
            return None

        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds.valid:
            from google.auth.transport.requests import Request
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())

        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        q = "mimeType='application/vnd.google-apps.folder' and trashed=false and name='%s'" % desired_name.replace("'", "\\'")
        res = service.files().list(q=q, fields="files(id,name)", pageSize=10,
                                   supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        return desired_name.strip() if len(files) == 1 else None
    except Exception:
        return None
