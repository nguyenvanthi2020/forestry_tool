# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer, QgsProcessingParameterRasterDestination,
    QgsProcessingParameterNumber, QgsProcessingParameterEnum,
    QgsProcessingParameterBoolean, QgsProcessingException
)
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from osgeo import gdal

def _tr(s):
    return QCoreApplication.translate("RasterOutlierFilterSingle", s)

class RasterOutlierFilterSingle(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    METHOD = "METHOD"
    THRESHOLD = "THRESHOLD"
    WINDOW = "WINDOW"
    MARK_ONLY = "MARK_ONLY"
    USE_BAND_NODATA = "USE_BAND_NODATA"
    TILE_SIZE = "TILE_SIZE"
    COMPRESSION = "COMPRESSION"
    BIGTIFF = "BIGTIFF"
    OUTPUT_DTYPE = "OUTPUT_DTYPE"

    METHODS = [
        _tr("Mean"),     # 0
        _tr("Median"),     # 1
        _tr("Nearest Neighbor")    # 2
    ]
    COMPRESSION_OPTS = ["LZW", "DEFLATE", "NONE", "PACKBITS"]
    BIGTIFF_OPTS = ["AUTO", "YES", "NO"]

    # Output dtype options
    DTYPE_OPTS = [
        _tr("Giữ nguyên theo band đầu vào"),
        "Byte", "UInt16", "Int16", "UInt32", "Int32", "Float32", "Float64"
    ]
    DTYPE_MAP = {
        "Byte": gdal.GDT_Byte,
        "UInt16": gdal.GDT_UInt16,
        "Int16": gdal.GDT_Int16,
        "UInt32": gdal.GDT_UInt32,
        "Int32": gdal.GDT_Int32,
        "Float32": gdal.GDT_Float32,
        "Float64": gdal.GDT_Float64,
    }

    def name(self): return "raster_outlier_filter_single"
    def displayName(self): return _tr("Xử lý điểm ảnh bất thường (đơn Band)")
    def group(self): return _tr("Tiện ích Raster")
    def groupId(self): return "raster_utils"
    def shortHelpString(self):
        return _tr(
            "Bộ lọc điểm ảnh bất thường (outlier) cho raster 1 band.\n\n"
            "📌 Các tham số:\n"
            "• INPUT: Raster đầu vào (yêu cầu 1 band, có thể nhiều band nhưng chỉ xử lý band 1).\n"
            "• OUTPUT: Đường dẫn raster đầu ra (GeoTIFF).\n"
            "• METHOD: Thuật toán phát hiện/thay thế outlier:\n"
            "    - Mean hàng xóm (loại tâm): So sánh với trung bình lân cận, loại trừ giá trị tâm.\n"
            "    - Median hàng xóm (robust): So sánh với trung vị lân cận, ít nhạy với nhiễu.\n"
            "    - Pixel hợp lệ gần Mean nhất: Thay thế outlier bằng pixel hợp lệ gần nhất theo giá trị trung bình.\n"
            "• THRESHOLD (Ngưỡng sigma): Hệ số k; pixel được coi là outlier nếu lệch quá k lần độ lệch chuẩn.\n"
            "• WINDOW: Kích thước cửa sổ lọc (số lẻ ≥ 3), ví dụ 3 = cửa sổ 3x3, 5 = 5x5.\n"
            "• MARK_ONLY: Nếu chọn True → chỉ tạo raster mặt nạ (0 = bình thường, 1 = outlier), kiểu Byte.\n"
            "• USE_BAND_NODATA: Có tôn trọng giá trị NoData gốc của band hay không.\n"
            "• TILE_SIZE: Kích thước block xử lý (px). Raster lớn sẽ được chia thành nhiều tile, mặc định 2048.\n"
            "• COMPRESSION: Kiểu nén GeoTIFF đầu ra (LZW, DEFLATE, NONE, PACKBITS).\n"
            "• BIGTIFF: Tùy chọn BigTIFF (AUTO, YES, NO) – cần thiết khi file > 4 GB.\n"
            "• OUTPUT_DTYPE: Kiểu dữ liệu đầu ra:\n"
            "    - Giữ nguyên theo band vào (mặc định), hoặc ép về Byte, UInt16, Int16, UInt32, Int32, Float32, Float64.\n"
            "    - Nếu MARK_ONLY bật, luôn xuất Byte.\n\n"
            "✅ Thuật toán chạy theo tile + đa luồng → xử lý nhanh và ổn định cho raster lớn.\n"
            "✅ Đảm bảo tôn trọng NoData; hỗ trợ BigTIFF và các kiểu nén GeoTIFF chuẩn."
        )

    def createInstance(self): return RasterOutlierFilterSingle()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, _tr("Raster đầu vào (1 band)")
        ))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, _tr("Raster đầu ra")
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.METHOD, _tr("Thuật toán thay thế"),
            options=self.METHODS, defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.THRESHOLD, _tr("Ngưỡng sigma"),
            type=QgsProcessingParameterNumber.Double, defaultValue=3.0, minValue=0
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.WINDOW, _tr("Kích thước cửa sổ (lẻ ≥ 3)"),
            type=QgsProcessingParameterNumber.Integer, defaultValue=3, minValue=3
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.MARK_ONLY, _tr("Chỉ tạo mặt nạ (0/1)"), defaultValue=False
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.USE_BAND_NODATA, _tr("Dùng NoData của band nếu có"), defaultValue=True
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.TILE_SIZE, _tr("Kích thước TILE (px)"),
            type=QgsProcessingParameterNumber.Integer, defaultValue=2048, minValue=512
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.COMPRESSION, _tr("Nén GeoTIFF"),
            options=self.COMPRESSION_OPTS, defaultValue=0  # LZW
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.BIGTIFF, _tr("BigTIFF"),
            options=self.BIGTIFF_OPTS, defaultValue=0  # AUTO
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.OUTPUT_DTYPE, _tr("Kiểu dữ liệu đầu ra"),
            options=self.DTYPE_OPTS, defaultValue=0  # Keep input
        ))

    # ---------- dtype helpers ----------
    @staticmethod
    def _dtype_range(gdal_type):
        if gdal_type == gdal.GDT_Byte:   return (0, 255, True)
        if gdal_type == gdal.GDT_UInt16: return (0, 65535, True)
        if gdal_type == gdal.GDT_Int16:  return (-32768, 32767, True)
        if gdal_type == gdal.GDT_UInt32: return (0, 4294967295, True)
        if gdal_type == gdal.GDT_Int32:  return (-2147483648, 2147483647, True)
        if gdal_type == gdal.GDT_Float32:return (np.finfo(np.float32).min, np.finfo(np.float32).max, False)
        if gdal_type == gdal.GDT_Float64:return (np.finfo(np.float64).min, np.finfo(np.float64).max, False)
        return (np.finfo(np.float32).min, np.finfo(np.float32).max, False)

    # ---------- local filters ----------
    @staticmethod
    def _mean_std_excluding_center(arr_f32, win):
        # import nội bộ để tránh load SciPy khi không dùng method này
        from scipy.ndimage import uniform_filter
        valid = np.isfinite(arr_f32).astype(np.float32)
        safe  = np.where(np.isfinite(arr_f32), arr_f32, 0.0).astype(np.float32)

        sum_win = uniform_filter(safe,  size=win, mode="nearest") * (win * win)
        cnt_win = uniform_filter(valid, size=win, mode="nearest") * (win * win)

        center_val   = np.where(np.isfinite(arr_f32), arr_f32, 0.0)
        center_count = np.isfinite(arr_f32).astype(np.float32)

        sum_nb = sum_win - center_val
        cnt_nb = np.maximum(cnt_win - center_count, 0.0)

        with np.errstate(divide="ignore", invalid="ignore"):
            mean_nb = np.where(cnt_nb > 0, sum_nb / cnt_nb, np.nan).astype(np.float32)

        sum2_win = uniform_filter(safe * safe, size=win, mode="nearest") * (win * win)
        sum2_nb  = sum2_win - (center_val * center_val)

        with np.errstate(divide="ignore", invalid="ignore"):
            ex2 = np.where(cnt_nb > 0, sum2_nb / cnt_nb, np.nan)
            var = ex2 - (mean_nb.astype(np.float64) ** 2)
        var = np.where(cnt_nb > 0, np.maximum(var, 0.0), np.nan)
        std_nb = np.sqrt(var, dtype=np.float64).astype(np.float32)
        return mean_nb, std_nb

    @staticmethod
    def _median_tile(arr_f32, win):
        from scipy.ndimage import median_filter
        valid = np.isfinite(arr_f32)
        filled = np.where(valid, arr_f32, 0.0).astype(np.float32)
        med = median_filter(filled, size=win, mode="nearest")
        return med.astype(np.float32)

    @staticmethod
    def _nearest_replace(arr_f32, mask_out):
        from scipy.ndimage import distance_transform_edt
        invalid = np.isnan(arr_f32)
        temp_invalid = invalid | mask_out
        _, inds = distance_transform_edt(temp_invalid, return_indices=True)
        repl = arr_f32[tuple(inds)]
        out = arr_f32.copy()
        out[mask_out] = repl[mask_out]
        return out

    # ---------- worker ----------
    def _process_tile(self, src_path, band_index, xoff, yoff, xsize, ysize, pad, method, thr, win, nodata, mark_only):
        rxoff = max(0, xoff - pad)
        ryoff = max(0, yoff - pad)

        ds_local = gdal.Open(src_path, gdal.GA_ReadOnly)
        if ds_local is None:
            raise RuntimeError("GDAL Open failed in worker")
        try:
            band = ds_local.GetRasterBand(band_index)
            rxend = min(band.XSize, xoff + xsize + pad)
            ryend = min(band.YSize, yoff + ysize + pad)
            rxs = rxend - rxoff
            rys = ryend - ryoff

            # đọc mảng (1 lần retry nếu lỗi IO)
            for attempt in range(2):
                try:
                    arr = band.ReadAsArray(rxoff, ryoff, rxs, rys)
                    if arr is None:
                        raise RuntimeError("ReadAsArray returned None")
                    arr = arr.astype(np.float32, copy=False)
                    break
                except Exception:
                    if attempt == 0:
                        continue
                    raise

            if nodata is not None:
                arr[arr == nodata] = np.nan

            if method == 0:  # mean
                mean_nb, std_nb = self._mean_std_excluding_center(arr, win)
                center = arr
                std_ok = np.isfinite(std_nb) & (std_nb > 0)
                mask_out = np.isfinite(center) & std_ok & (np.abs(center - mean_nb) > thr * std_nb)
                if mark_only:
                    filt = mask_out.astype(np.uint8)
                else:
                    filt = arr.copy()
                    filt[mask_out] = mean_nb[mask_out]

            elif method == 1:  # median
                med = self._median_tile(arr, win)
                mean_nb, std_nb = self._mean_std_excluding_center(arr, win)
                center = arr
                std_ok = np.isfinite(std_nb) & (std_nb > 0)
                mask_out = np.isfinite(center) & std_ok & (np.abs(center - mean_nb) > thr * std_nb)
                if mark_only:
                    filt = mask_out.astype(np.uint8)
                else:
                    filt = arr.copy()
                    filt[mask_out] = med[mask_out]

            else:  # nearest
                mean_nb, std_nb = self._mean_std_excluding_center(arr, win)
                center = arr
                std_ok = np.isfinite(std_nb) & (std_nb > 0)
                mask_out = np.isfinite(center) & std_ok & (np.abs(center - mean_nb) > thr * std_nb)
                if mark_only:
                    filt = mask_out.astype(np.uint8)
                else:
                    filt = self._nearest_replace(arr, mask_out)

            # cắt pad về kích thước tile gốc
            top = pad if ryoff < yoff else 0
            left = pad if rxoff < xoff else 0
            bottom = filt.shape[0] - pad if (ryoff + filt.shape[0]) > (yoff + ysize) else filt.shape[0]
            right  = filt.shape[1] - pad if (rxoff + filt.shape[1]) > (xoff + xsize) else filt.shape[1]
            out_tile = filt[top:bottom, left:right]
            return (xoff, yoff, out_tile)

        finally:
            ds_local = None

    def processAlgorithm(self, parameters, context, feedback):
        gdal.UseExceptions()

        rlayer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if rlayer is None or not rlayer.isValid():
            raise QgsProcessingException("Raster đầu vào không hợp lệ (cần 1 band)")

        src_path = rlayer.source()
        ds = gdal.Open(src_path, gdal.GA_ReadOnly)
        if ds is None:
            raise QgsProcessingException("GDAL không mở được raster")

        if ds.RasterCount < 1:
            raise QgsProcessingException("Raster không có band nào")
        if ds.RasterCount > 1:
            # Cho phép chạy trên band 1, nhưng cảnh báo người dùng
            feedback.pushInfo(_tr("Cảnh báo: Raster nhiều band, thuật toán sẽ xử lý band 1."))

        band_index = 1  # đơn band → band 1
        method = self.parameterAsEnum(parameters, self.METHOD, context)
        thr = self.parameterAsDouble(parameters, self.THRESHOLD, context)
        win = self.parameterAsInt(parameters, self.WINDOW, context)
        if win % 2 == 0: win += 1
        pad = win // 2

        mark_only = self.parameterAsBool(parameters, self.MARK_ONLY, context)
        use_nodata = self.parameterAsBool(parameters, self.USE_BAND_NODATA, context)
        tile_size = self.parameterAsInt(parameters, self.TILE_SIZE, context)
        comp = self.COMPRESSION_OPTS[self.parameterAsEnum(parameters, self.COMPRESSION, context)]
        bigtiff = self.BIGTIFF_OPTS[self.parameterAsEnum(parameters, self.BIGTIFF, context)]
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        out_dtype_idx = self.parameterAsEnum(parameters, self.OUTPUT_DTYPE, context)

        # Auto workers
        import multiprocessing
        workers = max(1, multiprocessing.cpu_count() - 1)

        band0 = ds.GetRasterBand(band_index)
        nodata = band0.GetNoDataValue() if use_nodata else None

        # chọn dtype đầu ra
        if mark_only:
            out_gdt = gdal.GDT_Byte
        else:
            if out_dtype_idx == 0:  # theo band vào
                out_gdt = band0.DataType
            else:
                sel = self.DTYPE_OPTS[out_dtype_idx]
                out_gdt = self.DTYPE_MAP[sel]
        out_min, out_max, out_is_int = self._dtype_range(out_gdt)

        driver = gdal.GetDriverByName("GTiff")
        creation_opts = ["TILED=YES", "INTERLEAVE=BAND", f"BIGTIFF={bigtiff}"]
        if comp != "NONE":
            creation_opts.append("COMPRESS=" + comp)

        out_ds = driver.Create(out_path, ds.RasterXSize, ds.RasterYSize, 1, out_gdt, options=creation_opts)
        if out_ds is None:
            raise QgsProcessingException("Không tạo được file đầu ra")
        out_ds.SetGeoTransform(ds.GetGeoTransform())
        out_ds.SetProjection(ds.GetProjection())

        out_band = out_ds.GetRasterBand(1)
        nodata_write = None
        if nodata is not None and not mark_only:
            if out_is_int and (nodata < out_min or nodata > out_max):
                out_band.SetNoDataValue(out_min)
                nodata_write = out_min
            else:
                out_band.SetNoDataValue(nodata)
                nodata_write = nodata

        width, height = ds.RasterXSize, ds.RasterYSize
        tiles = [(x, y, min(tile_size, width - x), min(tile_size, height - y))
                 for y in range(0, height, tile_size)
                 for x in range(0, width, tile_size)]

        total_jobs = len(tiles)
        done_jobs = 0

        results = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [
                ex.submit(self._process_tile, src_path, band_index, x, y, w, h, pad, method, thr, win, nodata, mark_only)
                for (x, y, w, h) in tiles
            ]
            for fut in as_completed(futs):
                xoff, yoff, out_tile = fut.result()
                results.append((xoff, yoff, out_tile))
                done_jobs += 1
                feedback.setProgress(int(100.0 * done_jobs / max(1, total_jobs)))

        # ghi tuần tự (và ép kiểu theo lựa chọn)
        for xoff, yoff, out_tile in results:
            if mark_only:
                out_arr = np.where(np.isfinite(out_tile), out_tile, 0).astype(np.uint8, copy=False)
            else:
                out_arr = out_tile
                if nodata_write is not None:
                    out_arr = np.where(np.isfinite(out_arr), out_arr, nodata_write)
                if out_is_int:
                    out_arr = np.rint(out_arr)
                    out_arr = np.clip(out_arr, out_min, out_max)
                    if out_gdt == gdal.GDT_Byte:
                        out_arr = out_arr.astype(np.uint8, copy=False)
                    elif out_gdt == gdal.GDT_UInt16:
                        out_arr = out_arr.astype(np.uint16, copy=False)
                    elif out_gdt == gdal.GDT_Int16:
                        out_arr = out_arr.astype(np.int16, copy=False)
                    elif out_gdt == gdal.GDT_UInt32:
                        out_arr = out_arr.astype(np.uint32, copy=False)
                    else:
                        out_arr = out_arr.astype(np.int32, copy=False)
                else:
                    if out_gdt == gdal.GDT_Float32:
                        out_arr = out_arr.astype(np.float32, copy=False)
                    else:
                        out_arr = out_arr.astype(np.float64, copy=False)

            out_band.WriteArray(out_arr, xoff, yoff)

        out_band.FlushCache()
        out_ds.FlushCache()
        out_ds = None
        ds = None
        return {self.OUTPUT: out_path}
