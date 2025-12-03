# -*- coding: utf-8 -*-
"""
Download ERA5-Land/ERA5 by AOI (simplified region) with flexible temporal aggregation.
- AOI union -> repair -> simplify(max_error_m) to reduce region payload
- ISO 8601 (Qt.ISODate) for filterDate
- Direct download via getDownloadURL to local folder
- Calendar-based band names:
  Hourly:  Gio YYYY-MM-DD HH
  Daily:   Ngay YYYY-MM-DD
  Monthly: Thang YYYY-MM
  Yearly:  Nam YYYY
- relative_humidity_2m derived from temperature_2m & dewpoint (Magnus)
- Aggregation options: mean / sum / min / max / median
- Band count guard: BAND_LIMIT + STOP_IF_OVER_LIMIT
- Hard clip to AOI (mask outside to NoData = -9999)
- Dataset-aware band name mapping (ERA5-Land/HOURLY vs ERA5/DAILY)
- Graceful handling of EE request-size limit: optional fallback Export to Google Drive
- total_precipitation converted from m to mm
- NEW: Write a VRT next to TIF with per-band calendar names for QGIS display

Author: Nguyen Van Thi + ChatGPT (OpenAI)
Date: 2025-10-06
"""

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterEnum,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterNumber,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterString,
    QgsProcessingException,
    QgsFeatureRequest,
    QgsWkbTypes,
)
import os, json, urllib.request
from xml.sax.saxutils import escape as _xml_escape

# GDAL (để tạo VRT với band names)
try:
    from osgeo import gdal
except Exception:
    gdal = None


# ---------------------------------------------------------------------
# Earth Engine init
# ---------------------------------------------------------------------
def _try_init_ee():
    try:
        import ee
    except Exception as e:
        raise QgsProcessingException(
            "Chưa cài thư viện 'earthengine-api'. Hãy cài trong Python của QGIS:\n"
            "pip install earthengine-api\nLỗi: {}".format(e)
        )
    try:
        ee.Initialize()
    except Exception:
        ee.Authenticate()
        ee.Initialize()
    return ee


def _fc_to_ee_geometry(source):
    """Convert QgsVectorLayer feature source to ee.Geometry (union all features)."""
    geom = None
    for f in source.getFeatures(QgsFeatureRequest()):
        g = f.geometry()
        if g is None or g.isEmpty():
            continue
        if geom is None:
            geom = g
        else:
            geom = geom.combine(g)
    if geom is None or geom.isEmpty():
        raise QgsProcessingException("Lớp AOI không có hình học hợp lệ.")
    if QgsWkbTypes.geometryType(geom.wkbType()) != QgsWkbTypes.PolygonGeometry:
        geom = geom.buffer(10, 8)  # tạo polygon nếu là line/point
    gj = json.loads(geom.asJson())
    import ee
    return ee.Geometry(gj)


# ---------------------------------------------------------------------
# Main Algorithm
# ---------------------------------------------------------------------
class DownloadERA5GenericAlgorithm(QgsProcessingAlgorithm):
    """
    Tải dữ liệu ERA5 / ERA5-Land theo AOI (đã đơn giản hóa), cho phép:
    - Chọn biến (variable) bằng drop-list
    - Chọn thời gian bắt đầu - kết thúc
    - Tổng hợp theo giờ / ngày / tháng / năm
    - Chọn phép tổng hợp: mean/sum/min/max/median
    - Đổi đơn vị nhiệt độ (K hoặc °C) nếu là biến nhiệt độ (chỉ với mean)
    - Band đặt tên theo lịch: Gio/Ngay/Thang/Nam
    - Tự tính RH nếu chọn 'relative_humidity_2m'
    - Giới hạn số band để tránh file quá lớn
    - Mask ngoài ranh về NoData = -9999
    - Dataset-aware band mapping
    - Nếu yêu cầu tải trực tiếp quá lớn: tùy chọn tự động Export to Google Drive
    - NEW: sinh file .vrt kèm tên band theo lịch để QGIS hiển thị đúng
    """

    PARAM_AOI = "AOI"
    PARAM_DATASET = "DATASET"
    PARAM_VARIABLE = "VARIABLE"
    PARAM_TEMPORAL = "TEMPORAL"
    PARAM_AGG = "AGG_FUNC"
    PARAM_TEMPUNIT = "TEMPUNIT"
    PARAM_START = "START_DATE"
    PARAM_END = "END_DATE"
    PARAM_SCALE = "SCALE"
    PARAM_CRS = "CRS"
    PARAM_OUTDIR = "OUTDIR"
    PARAM_BAND_LIMIT = "BAND_LIMIT"
    PARAM_STOP_IF_OVER = "STOP_IF_OVER_LIMIT"
    PARAM_EXPORT_DRIVE_IF_LARGE = "EXPORT_DRIVE_IF_LARGE"
    PARAM_DRIVE_FOLDER = "DRIVE_FOLDER"
    PARAM_DRIVE_PREFIX = "DRIVE_PREFIX"

    DATASET_CHOICES = ["ERA5-Land (hourly)", "ERA5 (reanalysis) DAILY"]
    TEMPORAL_CHOICES = ["Theo giờ", "Theo ngày", "Theo tháng", "Theo năm"]
    TEMPUNIT_CHOICES = ["Kelvin (K)", "Độ C (°C)"]
    AGG_CHOICES = ["Mean", "Sum", "Min", "Max", "Median"]

    VARIABLE_LIST = [
        "temperature_2m",
        "dewpoint_temperature_2m",
        "relative_humidity_2m",  # tính từ T & Td
        "total_precipitation",
        "u_component_of_wind_10m",
        "v_component_of_wind_10m",
        "surface_pressure",
        "surface_solar_radiation_downwards",
        "potential_evaporation",
        "soil_temperature_level_1",
        "volumetric_soil_water_layer_1",
    ]

    # ----------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def name(self):
        return "download_era5_generic"

    def displayName(self):
        return self.tr("Tải dữ liệu thời tiết ERA5")

    def group(self):
        return self.tr("Tiện ích Raster")

    def groupId(self):
        return "raster_utils"

    def shortHelpString(self):
        return self.tr(
            """
Tải dữ liệu ERA5 / ERA5-Land theo lớp AOI:
• Drop-list chọn biến ERA5
• Chọn thời gian (ISO 8601), bậc tổng hợp: giờ/ngày/tháng/năm
• Chọn phép tổng hợp: mean / sum / min / max / median
• Band đặt tên theo lịch: Gio/Ngay/Thang/Nam
• Đơn vị nhiệt độ (K/°C) tự bật/tắt; với biến nhiệt độ, phép tổng hợp sẽ buộc là mean
• 'relative_humidity_2m' được tính từ T & Td (Magnus)
• Mapping band theo dataset; nếu dung lượng tải về vượt ngưỡng quy định của Google Earth Engine thì tự động chuyển sang chế độ lưu vào Google Drive

"""
        )

    def createInstance(self):
        return DownloadERA5GenericAlgorithm()

    # ----------------------------------------------------------
    def initAlgorithm(self, config=None):
        from qgis.core import QgsProcessingParameterEnum

        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.PARAM_AOI, "Ranh giới quan tâm (AOI)", [QgsProcessing.TypeVectorPolygon]
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_DATASET, "Nguồn dữ liệu", self.DATASET_CHOICES, defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_VARIABLE,
                "Chọn biến ERA5 cần tải",
                options=self.VARIABLE_LIST,
                allowMultiple=False,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_TEMPORAL, "Kiểu tổng hợp thời gian", self.TEMPORAL_CHOICES, defaultValue=2
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_AGG, "Phép tổng hợp",
                self.AGG_CHOICES, defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_TEMPUNIT,
                "Đơn vị nhiệt độ (chỉ với biến nhiệt độ)",
                self.TEMPUNIT_CHOICES,
                defaultValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.PARAM_START, "Ngày bắt đầu",
                QgsProcessingParameterDateTime.DateTime, defaultValue="2024-01-01T00:00:00Z"
            )
        )
        self.addParameter(
            QgsProcessingParameterDateTime(
                self.PARAM_END, "Ngày kết thúc",
                QgsProcessingParameterDateTime.DateTime, defaultValue="2024-12-31T23:59:59Z"
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.PARAM_SCALE, "Scale (m)",
                QgsProcessingParameterNumber.Double, defaultValue=10000, minValue=1
            )
        )
        self.addParameter(QgsProcessingParameterCrs(self.PARAM_CRS, "CRS đầu ra", "EPSG:4326"))
        self.addParameter(
            QgsProcessingParameterFolderDestination(self.PARAM_OUTDIR, "Thư mục lưu kết quả")
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.PARAM_BAND_LIMIT, "Giới hạn tối đa số band (cảnh báo/dừng)",
                QgsProcessingParameterNumber.Integer, defaultValue=500, minValue=1
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAM_STOP_IF_OVER, "Dừng nếu vượt quá giới hạn band?", defaultValue=True
            )
        )
        # Export to Drive fallback
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAM_EXPORT_DRIVE_IF_LARGE,
                "Nếu yêu cầu quá lớn, tự động Export to Google Drive",
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.PARAM_DRIVE_FOLDER,
                "Thư mục trên Google Drive (nếu Export)",
                defaultValue="QGIS_ERA5"
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.PARAM_DRIVE_PREFIX,
                "Tiền tố tên file trên Drive (tùy chọn)",
                defaultValue=""
            )
        )

    # ----------------------------------------------------------
    def updateParameters(self, parameters, context, feedback):
        """Bật/tắt tùy chọn đơn vị nhiệt độ khi biến là loại temperature."""
        try:
            var_index = parameters[self.PARAM_VARIABLE]
            if var_index is not None:
                var_index = int(var_index)
                variable = self.VARIABLE_LIST[var_index]
            else:
                variable = None
        except Exception:
            variable = None

        is_temp = variable and ("temperature" in variable.lower() or "temp" in variable.lower())

        temp_param = self.parameterDefinition(self.PARAM_TEMPUNIT)
        if temp_param:
            temp_param.setIsEnabled(is_temp)
        return parameters

    # ----------------------------------------------------------
    def processAlgorithm(self, params, context, feedback):
        # ---- Lấy tham số
        src = self.parameterAsSource(params, self.PARAM_AOI, context)
        dataset = int(self.parameterAsEnum(params, self.PARAM_DATASET, context))
        var_index = int(self.parameterAsEnum(params, self.PARAM_VARIABLE, context))
        variable_user = self.VARIABLE_LIST[var_index]
        temporal = int(self.parameterAsEnum(params, self.PARAM_TEMPORAL, context))
        agg_idx = int(self.parameterAsEnum(params, self.PARAM_AGG, context))
        agg_name = ["mean","sum","min","max","median"][agg_idx]
        tempunit = int(self.parameterAsEnum(params, self.PARAM_TEMPUNIT, context))

        start_dt = self.parameterAsDateTime(params, self.PARAM_START, context).toUTC()
        end_dt = self.parameterAsDateTime(params, self.PARAM_END, context).toUTC()
        start_iso = start_dt.toString(Qt.ISODate)
        end_iso = end_dt.toString(Qt.ISODate)

        scale = float(self.parameterAsDouble(params, self.PARAM_SCALE, context))
        crs = self.parameterAsCrs(params, self.PARAM_CRS, context)
        outdir = self.parameterAsString(params, self.PARAM_OUTDIR, context)

        band_limit = int(self.parameterAsInt(params, self.PARAM_BAND_LIMIT, context))
        stop_if_over = bool(self.parameterAsBool(params, self.PARAM_STOP_IF_OVER, context))

        export_drive_if_large = bool(self.parameterAsBool(params, self.PARAM_EXPORT_DRIVE_IF_LARGE, context))
        drive_folder = self.parameterAsString(params, self.PARAM_DRIVE_FOLDER, context) or "QGIS_ERA5"
        drive_prefix = self.parameterAsString(params, self.PARAM_DRIVE_PREFIX, context) or ""

        if not os.path.exists(outdir):
            os.makedirs(outdir)

        ee = _try_init_ee()

        # ---- Tạo vùng: union -> repair -> simplify (hoặc bbox)
        region = _fc_to_ee_geometry(src)
        region = region.buffer(1, 100).buffer(-1, 100)  # repair, tránh error margin = 0

        max_error_m = 3000
        use_bbox = False
        if use_bbox:
            region_final = region.bounds()
            feedback.pushInfo("Đang dùng BBOX của AOI để tải/clip.")
        else:
            region_final = region.simplify(max_error_m)
            feedback.pushInfo(f"Đang dùng vùng đã đơn giản hóa (maxError: {max_error_m} m).")

        # ---- Chọn dataset & kiểm tra tương thích temporal
        if dataset == 0:
            ds_id = "ECMWF/ERA5_LAND/HOURLY"
        else:
            ds_id = "ECMWF/ERA5/DAILY"
            if temporal == 0:
                raise QgsProcessingException(
                    "Dataset 'ERA5 (reanalysis) DAILY' không hỗ trợ 'Theo giờ'. "
                    "Hãy chọn 'Theo ngày/tháng/năm' hoặc chuyển sang 'ERA5-Land (hourly)'."
                )

        feedback.pushInfo(f"Sử dụng dataset: {ds_id}")
        feedback.pushInfo(f"Khoảng thời gian: {start_iso} → {end_iso}")
        feedback.pushInfo(f"Phép tổng hợp: {agg_name}")

        st = ee.Date(start_iso)
        en = ee.Date(end_iso)

        # ---- Mapping tên band theo dataset (đặc biệt cho ERA5/DAILY)
        def map_var_name_for_dataset(var_user, ds_flag_daily):
            if ds_flag_daily:
                daily_map = {
                    "temperature_2m": "mean_2m_air_temperature",
                    "dewpoint_temperature_2m": "dewpoint_2m_temperature",
                    "total_precipitation": "total_precipitation",
                    "surface_pressure": "surface_pressure",
                    "u_component_of_wind_10m": "u_component_of_wind_10m",
                    "v_component_of_wind_10m": "v_component_of_wind_10m",
                }
                if var_user == "relative_humidity_2m":
                    return "relative_humidity_2m"  # xử lý riêng
                if var_user not in daily_map:
                    raise QgsProcessingException(
                        "Biến '{}' không có trong ERA5/DAILY. Biến hỗ trợ gồm: "
                        "temperature_2m, dewpoint_temperature_2m, total_precipitation, "
                        "surface_pressure, u_component_of_wind_10m, v_component_of_wind_10m, "
                        "hoặc 'relative_humidity_2m' (tính toán).".format(var_user)
                    )
                return daily_map[var_user]
            else:
                return var_user

        var_for_ds = map_var_name_for_dataset(variable_user, dataset == 1)

        # ---- Nếu RH: cần T & Td; buộc mean + chọn đúng band theo dataset
        if variable_user == "relative_humidity_2m":
            if agg_name != "mean":
                feedback.reportError("Biến 'relative_humidity_2m' chỉ có ý nghĩa với mean. Tự động dùng mean.")
            agg_name = "mean"
            if dataset == 0:
                ic = ee.ImageCollection(ds_id).select(
                    ["temperature_2m", "dewpoint_temperature_2m"]
                ).filterDate(st, en)
            else:
                ic_raw = ee.ImageCollection(ds_id).select(
                    ["mean_2m_air_temperature", "dewpoint_2m_temperature"]
                ).filterDate(st, en)
                ic = ic_raw.map(
                    lambda im: im.select(
                        ["mean_2m_air_temperature", "dewpoint_2m_temperature"],
                        ["temperature_2m", "dewpoint_temperature_2m"]
                    )
                )
        else:
            ic = ee.ImageCollection(ds_id).select(var_for_ds).filterDate(st, en)

        # ---- Nếu biến là nhiệt độ & agg != mean: buộc mean (để đổi đơn vị đúng)
        if (variable_user != "relative_humidity_2m") and (
            ("temperature" in variable_user.lower()) or ("temp" in variable_user.lower())
        ):
            if agg_name != "mean":
                feedback.reportError(f"Biến nhiệt độ không phù hợp với phép '{agg_name}'. Tự động dùng 'mean'.")
                agg_name = "mean"

        # ---- Map phép tổng hợp cho ImageCollection
        def reduce_ic(ic_sub, agg):
            if agg == "mean":
                return ic_sub.mean()
            elif agg == "sum":
                return ic_sub.sum()
            elif agg == "min":
                return ic_sub.min()
            elif agg == "max":
                return ic_sub.max()
            elif agg == "median":
                return ic_sub.median()
            else:
                return ic_sub.mean()

        # ---- Helper: sequence 0..N-1 theo lịch, bao gồm điểm cuối (floor + 1)
        def safe_seq(count):
            count = ee.Number(count)
            return ee.Algorithms.If(
                count.lte(0),
                ee.List([]),
                ee.List.sequence(0, count.subtract(1))
            )

        # ---- Hàm tính RH từ T & Td (°C) theo Magnus/Tetens
        def rh_from_T_Td_C(image_with_T_Td):
            a = 17.625
            b = 243.04
            T_C  = image_with_T_Td.select('temperature_2m').subtract(273.15)
            Td_C = image_with_T_Td.select('dewpoint_temperature_2m').subtract(273.15)
            RH = ee.Image().expression(
                '100.0 * exp(a*Td/(b+Td)) / exp(a*T/(b+T))',
                {'T': T_C, 'Td': Td_C, 'a': a, 'b': b}
            ).clamp(0, 100)
            return RH.rename('relative_humidity_2m')

        # ---- Ước lượng số band theo lịch (floor + 1)
        def estimate_band_count():
            h0 = st
            d0 = ee.Date.fromYMD(st.get('year'), st.get('month'), st.get('day'))
            m0 = ee.Date.fromYMD(st.get('year'), st.get('month'), 1)
            y0 = ee.Date.fromYMD(st.get('year'), 1, 1)
            if temporal == 0:
                return ee.Number(en.difference(h0, 'hour')).floor().add(1)
            elif temporal == 1:
                return ee.Number(en.difference(d0, 'day')).floor().add(1)
            elif temporal == 2:
                return ee.Number(en.difference(m0, 'month')).floor().add(1)
            else:
                return ee.Number(en.difference(y0, 'year')).floor().add(1)

        bands_est = estimate_band_count().getInfo() or 0
        if bands_est > band_limit:
            msg = f"Số band ước tính = {bands_est} > BAND_LIMIT = {band_limit}."
            if stop_if_over:
                raise QgsProcessingException(msg + " Vui lòng rút ngắn khoảng thời gian hoặc đổi bậc tổng hợp.")
            else:
                feedback.reportError(msg + " Vẫn tiếp tục theo yêu cầu (STOP_IF_OVER_LIMIT=False).")

        # ---- Dựng ảnh theo lịch + tên band (dùng mốc lịch gốc để đồng bộ)
        if temporal == 0:
            # THEO GIỜ: Gio YYYY-MM-DD HH
            h0 = st
            hours = ee.Number(en.difference(h0, 'hour')).floor().add(1)
            idx = ee.List(safe_seq(hours))

            def _hour_img(i):
                i = ee.Number(i)
                hStart = h0.advance(i, 'hour')
                hEnd   = hStart.advance(1, 'hour')
                sub = ic.filterDate(hStart, hEnd)
                if variable_user == "relative_humidity_2m":
                    sub = sub.map(lambda im: rh_from_T_Td_C(im))  # RH từng giờ
                im = reduce_ic(sub, agg_name)
                name = ee.String('Gio ').cat(hStart.format('YYYY-MM-dd HH'))
                return im.rename(name)

            imgCol = ee.ImageCollection(idx.map(_hour_img))
            img = imgCol.toBands()
            names_ee = idx.map(lambda i: ee.String('Gio ').cat(h0.advance(ee.Number(i), 'hour').format('YYYY-MM-dd HH')))
            img = img.rename(names_ee)
            tag = "hourly"

        elif temporal == 1:
            # THEO NGÀY: Ngay YYYY-MM-DD
            d0 = ee.Date.fromYMD(st.get('year'), st.get('month'), st.get('day'))
            days = ee.Number(en.difference(d0, 'day')).floor().add(1)
            idx = ee.List(safe_seq(days))

            def _day_img(i):
                i = ee.Number(i)
                dStart = d0.advance(i, 'day')
                dEnd   = dStart.advance(1, 'day')
                sub = ic.filterDate(dStart, dEnd)
                if variable_user == "relative_humidity_2m":
                    sub = sub.map(lambda im: rh_from_T_Td_C(im))
                im = reduce_ic(sub, agg_name)
                name = ee.String('Ngay ').cat(dStart.format('YYYY-MM-dd'))
                return im.rename(name)

            imgCol = ee.ImageCollection(idx.map(_day_img))
            img = imgCol.toBands()
            names_ee = idx.map(lambda i: ee.String('Ngay ').cat(d0.advance(ee.Number(i), 'day').format('YYYY-MM-dd')))
            img = img.rename(names_ee)
            tag = "daily"

        elif temporal == 2:
            # THEO THÁNG: Thang YYYY-MM
            m0 = ee.Date.fromYMD(st.get('year'), st.get('month'), 1)
            months = ee.Number(en.difference(m0, 'month')).floor().add(1)
            idx = ee.List(safe_seq(months))

            def _month_img(i):
                i = ee.Number(i)
                mStart = m0.advance(i, 'month')  # đã là đầu tháng
                mEnd   = mStart.advance(1, 'month')
                sub = ic.filterDate(mStart, mEnd)
                if variable_user == "relative_humidity_2m":
                    sub = sub.map(lambda im: rh_from_T_Td_C(im))
                im = reduce_ic(sub, agg_name)
                name = ee.String('Thang ').cat(mStart.format('YYYY-MM'))
                return im.rename(name)

            imgCol = ee.ImageCollection(idx.map(_month_img))
            img = imgCol.toBands()
            names_ee = idx.map(lambda i: ee.String('Thang ').cat(m0.advance(ee.Number(i), 'month').format('YYYY-MM')))
            img = img.rename(names_ee)
            tag = "monthly"

        else:
            # THEO NĂM: Nam YYYY
            y0 = ee.Date.fromYMD(st.get('year'), 1, 1)
            years = ee.Number(en.difference(y0, 'year')).floor().add(1)
            idx = ee.List(safe_seq(years))

            def _year_img(i):
                i = ee.Number(i)
                yStart = y0.advance(i, 'year')
                yEnd   = yStart.advance(1, 'year')
                sub = ic.filterDate(yStart, yEnd)
                if variable_user == "relative_humidity_2m":
                    sub = sub.map(lambda im: rh_from_T_Td_C(im))
                im = reduce_ic(sub, agg_name)
                name = ee.String('Nam ').cat(yStart.format('YYYY'))
                return im.rename(name)

            imgCol = ee.ImageCollection(idx.map(_year_img))
            img = imgCol.toBands()
            names_ee = idx.map(lambda i: ee.String('Nam ').cat(y0.advance(ee.Number(i), 'year').format('YYYY')))
            img = img.rename(names_ee)
            tag = "yearly"

        # ---- Kiểm tra số band thực tế & lấy danh sách tên band để ghi VRT
        band_names = img.bandNames()
        band_count = band_names.size().getInfo() or 0
        if band_count == 0:
            raise QgsProcessingException(
                "Kết quả không có band nào sau khi tổng hợp. "
                "Có thể do chọn biến không tồn tại trong dataset '{}'. ".format(ds_id)
            )
        band_names_py = band_names.getInfo()

        # ---- Đổi đơn vị cho biến nhiệt độ (chỉ khi agg=mean)
        if (variable_user != "relative_humidity_2m") and (("temperature" in variable_user.lower()) or ("temp" in variable_user.lower())):
            if agg_name == "mean":
                if tempunit == 1:  # °C
                    img = img.subtract(273.15)
                    feedback.pushInfo("Đã chuyển đơn vị sang °C (mean).")
                else:
                    feedback.pushInfo("Giữ đơn vị Kelvin (K).")
            else:
                feedback.reportError("Bỏ qua đổi đơn vị vì phép tổng hợp không phải mean (đã buộc mean ở trên).")

        # ---- Đổi đơn vị cho lượng mưa (m -> mm)
        if variable_user == "total_precipitation":
            img = img.multiply(1000.0)
            feedback.pushInfo("Đã chuyển 'total_precipitation' sang milimét (mm).")

        # ---- Clip + Mask ngoài ranh → NoData = -9999
        region_mask = ee.Image.constant(1).clip(region_final)
        img = img.updateMask(region_mask).unmask(-9999)
        #img = img.clip(region)
        # ---- Hàm Export to Google Drive
        def export_to_drive(image, fname):
            import ee
            desc = f"QGIS_ERA5_{fname}"
            file_prefix = f"{drive_prefix}{fname}" if drive_prefix else fname
            task = ee.batch.Export.image.toDrive(
                image=image,
                description=desc,
                folder=drive_folder,
                fileNamePrefix=file_prefix,
                region=region_final,
                scale=scale,
                crs=crs.authid() if hasattr(crs, "authid") else "EPSG:4326",
                fileFormat="GeoTIFF",
                maxPixels=1e13
            )
            task.start()
            return task

        # ---- Viết VRT với tên band
        def write_vrt_with_bandnames(tif_path, names_list):
            if gdal is None:
                return None  # nếu không có GDAL python (hiếm trong QGIS), bỏ qua
            ds = gdal.Open(tif_path, gdal.GA_ReadOnly)
            if ds is None:
                return None
            xsize = ds.RasterXSize
            ysize = ds.RasterYSize
            gt = ds.GetGeoTransform()
            prj = ds.GetProjectionRef() or ""
            nb = ds.RasterCount
            # Bảo đảm số tên == số band
            if not names_list or len(names_list) != nb:
                names_list = [f"Band {i}" for i in range(1, nb + 1)]

            def _dtype_name(code):
                try:
                    return gdal.GetDataTypeName(code)
                except Exception:
                    return "Float32"

            # XML
            geotr = "{:.15f}, {:.15f}, {:.15f}, {:.15f}, {:.15f}, {:.15f}".format(
                gt[0], gt[1], gt[2], gt[3], gt[4], gt[5]
            ) if gt else ""
            tif_base = os.path.basename(tif_path)
            lines = []
            lines.append(f'<VRTDataset rasterXSize="{xsize}" rasterYSize="{ysize}">')
            if prj:
                lines.append(f"  <SRS>{_xml_escape(prj)}</SRS>")
            if geotr:
                lines.append(f"  <GeoTransform>{geotr}</GeoTransform>")
            for i in range(1, nb + 1):
                band = ds.GetRasterBand(i)
                dtype = _dtype_name(band.DataType) if band else "Float32"
                desc = _xml_escape(str(names_list[i - 1]))
                lines.append(f'  <VRTRasterBand dataType="{dtype}" band="{i}">')
                lines.append(f"    <Description>{desc}</Description>")
                lines.append(f"    <NoDataValue>-9999</NoDataValue>")
                lines.append("    <SimpleSource>")
                lines.append(f'      <SourceFilename relativeToVRT="1">{_xml_escape(tif_base)}</SourceFilename>')
                lines.append(f"      <SourceBand>{i}</SourceBand>")
                lines.append("    </SimpleSource>")
                lines.append("  </VRTRasterBand>")
            lines.append("</VRTDataset>")
            vrt_path = os.path.splitext(tif_path)[0] + ".vrt"
            with open(vrt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return vrt_path

        # ---- Tải ảnh trực tiếp về máy (có bắt lỗi dung lượng yêu cầu)
        def download_image(image, fname, names_list):
            try:
                url = image.getDownloadURL({
                    "name": fname,
                    "region": region_final,
                    "scale": scale,
                    "crs": crs.authid() if hasattr(crs, "authid") else "EPSG:4326",
                    "format": "GEO_TIFF",
                    # "maxPixels": 1e13
                })
            except Exception as e:
                msg = str(e)
                # Nhận diện lỗi vượt giới hạn dung lượng yêu cầu của EE
                if ("Total request size" in msg) or ("payload size exceeds the limit" in msg):
                    if export_drive_if_large:
                        feedback.reportError(
                            "YÊU CẦU QUÁ LỚN: chuyển qua Export to Google Drive theo tuỳ chọn."
                        )
                        task = export_to_drive(image, fname)
                        feedback.pushInfo(f"Đã khởi tạo tác vụ Export to Drive: {task.id}")
                        feedback.pushInfo(f"Drive folder: {drive_folder}")
                        feedback.pushInfo("Bạn có thể theo dõi trong Earth Engine Tasks hoặc Google Drive.")
                        return None, None
                    else:
                        raise QgsProcessingException(
                            "YÊU CẦU QUÁ LỚN: Earth Engine từ chối vì kích thước yêu cầu vượt giới hạn (~48–50 MB). "
                            "Hãy thử một hoặc nhiều cách sau:\n"
                            "• Rút ngắn khoảng thời gian hoặc tăng bậc tổng hợp (ví dụ Ngày → Tháng).\n"
                            "• Chia thời gian thành nhiều đợt (ví dụ 6 tháng/lần).\n"
                            "• Tăng 'Scale' (độ phân giải thô hơn, ví dụ 20000 m).\n"
                            "• Dùng vùng nhỏ hơn (hoặc bật BBOX), đơn giản hóa vùng mạnh hơn.\n"
                            "• Giảm số band.\n"
                            f"Chi tiết lỗi gốc: {msg}"
                        )
                raise

            dest = os.path.join(outdir, fname + ".tif")
            feedback.pushInfo(f"Đang tải: {dest}")
            try:
                urllib.request.urlretrieve(url, dest)
            except Exception as e2:
                raise QgsProcessingException(f"Lỗi khi tải file từ URL: {e2}")

            vrt_path = write_vrt_with_bandnames(dest, names_list)
            if vrt_path:
                feedback.pushInfo(f"Đã sinh VRT với tên band theo lịch: {vrt_path}")
            else:
                feedback.pushInfo("Không tạo được VRT (thiếu GDAL Python hoặc mở TIF thất bại).")
            return dest, vrt_path

        fname = f"{variable_user}_{tag}_{agg_name}_{start_dt.date().toString(Qt.ISODate)}_{end_dt.date().toString(Qt.ISODate)}"
        tif_path, vrt_path = download_image(img, fname, band_names_py)

        if tif_path is None:
            # Đã chuyển qua Export to Drive
            feedback.pushInfo("✅ Đã chuyển sang Export to Drive do yêu cầu quá lớn.")
            return {}

        feedback.pushInfo("✅ Hoàn thành tải ERA5 (vùng đơn giản, band theo lịch, aggregation, mask NoData, mapping theo dataset).")
        feedback.pushInfo(f"Lưu tại: {tif_path}")
        if vrt_path:
            feedback.pushInfo(f"Mở file này trong QGIS để thấy tên band đúng lịch: {vrt_path}")
        return {}
