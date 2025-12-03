# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterRasterLayer, QgsProcessingParameterBoolean,
    QgsProcessingParameterNumber, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterRasterDestination,
    QgsProcessingParameterString, QgsApplication, QgsVectorLayer,
    QgsWkbTypes
)
from qgis import processing


def _tr(s): return QCoreApplication.translate("StreamFromDEM", s)


class StreamFromDEM(QgsProcessingAlgorithm):
    # params
    DEM = "DEM"
    FILL_SINKS = "FILL_SINKS"
    THRESH = "THRESH"
    METHOD_ORDER = "METHOD_ORDER"
    MAKE_BASINS = "MAKE_BASINS"
    ENGINE = "ENGINE"
    EXTRA_OPTS = "EXTRA_OPTS"

    # smooth + filter
    SMOOTH_ENABLE = "SMOOTH_ENABLE"
    SMOOTH_METHOD = "SMOOTH_METHOD"
    SMOOTH_TOL = "SMOOTH_TOL"
    SMOOTH_ITER = "SMOOTH_ITER"
    MIN_LENGTH = "MIN_LENGTH"

    # order filter for vectorization
    ORDER_MIN = "ORDER_MIN"

    # outputs
    OUT_STREAMS = "OUT_STREAMS"    # vector line sink
    OUT_BASINS = "OUT_BASINS"      # polygon sink (optional)
    OUT_ACC = "OUT_ACC"            # raster
    OUT_STREAM_R = "OUT_STREAM_R"  # raster (0/1)
    OUT_ORDER_R = "OUT_ORDER_R"    # raster (Strahler)

    ENGINES = ["Auto (SAGA→GRASS→WBT)", "SAGA", "GRASS", "WhiteboxTools"]
    ORDERS = ["Strahler", "Shreve"]
    SMOOTH_METHODS = ["Douglas–Peucker (simplify)", "Chaikin (smooth)"]

    def tr(self, s): return _tr(s)
    def name(self): return "stream_network_from_dem"
    def displayName(self): return self.tr("Tạo mạng lưới và phân cấp sông suối từ DEM")
    def group(self): return self.tr("Tiện ích Raster")
    def groupId(self): return "raster_utils"
    def shortHelpString(self):
        return self.tr(
            "Sinh mạng lưới sông suối & phân cấp từ DEM. AUTO sẽ ưu tiên SAGA rồi tới GRASS/WBT.\n"
            "THRESH = ngưỡng tích luỹ (số ô). ORDER_MIN = cấp bậc tối thiểu để xuất vector.\n"
            "Khuyến nghị CRS theo mét để MIN_LENGTH, tolerance… có ý nghĩa không gian."
        )
    def createInstance(self): return StreamFromDEM()

    # ========= helper tạo Number param tương thích nhiều bản QGIS =========
    def _num(self, name, desc, ntype, default, minv=None, maxv=None, optional=False):
        """
        QGIS mới (3.22+): QgsProcessingParameterNumber(name, description, type=..., defaultValue=..., minValue=..., maxValue=..., optional=...)
        QGIS cũ (3.16):   QgsProcessingParameterNumber(name, description, type, defaultValue, optional, minValue, maxValue)

        Ghi chú:
        - KHÔNG truyền min/max nếu là None trên các bản mới (3.44 lỗi khi nhận None).
        - Có nhánh fallback dùng positional order cho các bản cũ.
        """
        # Thử chữ ký mới (kwargs) và CHỈ truyền những gì có giá trị
        try:
            kwargs = {"type": ntype, "defaultValue": default}
            if minv is not None:
                kwargs["minValue"] = minv
            if maxv is not None:
                kwargs["maxValue"] = maxv
            if optional:
                kwargs["optional"] = True
            return QgsProcessingParameterNumber(name, desc, **kwargs)
        except TypeError:
            # Fallback: chữ ký kiểu cũ (positional). Bổ sung min/max lớn khi thiếu.
            if minv is None:
                minv = -1e20 if ntype == QgsProcessingParameterNumber.Double else -2147483648
            if maxv is None:
                maxv = 1e20 if ntype == QgsProcessingParameterNumber.Double else 2147483647
            try:
                # (name, description, type, defaultValue, optional, minValue, maxValue)
                return QgsProcessingParameterNumber(name, desc, ntype, default, bool(optional), float(minv), float(maxv))
            except TypeError:
                # Phương án tối thiểu: không set min/max/optional
                return QgsProcessingParameterNumber(name, desc, ntype, default)

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, self.tr("DEM đầu vào")))
        self.addParameter(QgsProcessingParameterBoolean(self.FILL_SINKS, self.tr("Lấp hố trũng trước khi tính"), True))

        self.addParameter(self._num(
            self.THRESH, self.tr("Ngưỡng tích luỹ (số ô)"),
            QgsProcessingParameterNumber.Integer, default=1000, minv=1
        ))

        self.addParameter(QgsProcessingParameterEnum(self.METHOD_ORDER, self.tr("Kiểu phân cấp"),
                                                     options=self.ORDERS, defaultValue=0))
        self.addParameter(self._num(
            self.ORDER_MIN, self.tr("Cấp bậc tối thiểu (ví dụ ≥5)"),
            QgsProcessingParameterNumber.Integer, default=0, minv=0
        ))
        self.addParameter(QgsProcessingParameterBoolean(self.MAKE_BASINS, self.tr("Sinh lưu vực"), False))
        self.addParameter(QgsProcessingParameterEnum(self.ENGINE, self.tr("Bộ máy xử lý"),
                                                     options=self.ENGINES, defaultValue=0))
        self.addParameter(QgsProcessingParameterString(self.EXTRA_OPTS, self.tr("Tùy chọn nâng cao"),
                                                       defaultValue="", optional=True))
        # smooth & filter
        self.addParameter(QgsProcessingParameterBoolean(self.SMOOTH_ENABLE, self.tr("Làm trơn/simplify"), False))
        self.addParameter(QgsProcessingParameterEnum(self.SMOOTH_METHOD, self.tr("Phương pháp làm trơn"),
                                                     options=self.SMOOTH_METHODS, defaultValue=0))

        self.addParameter(self._num(
            self.SMOOTH_TOL, self.tr("Tolerance/Offset"),
            QgsProcessingParameterNumber.Double, default=10.0, minv=0.0
        ))
        self.addParameter(self._num(
            self.SMOOTH_ITER, self.tr("Số vòng (Chaikin)"),
            QgsProcessingParameterNumber.Integer, default=1, minv=1
        ))
        self.addParameter(self._num(
            self.MIN_LENGTH, self.tr("Chiều dài tối thiểu (CRS)"),
            QgsProcessingParameterNumber.Double, default=0.0, minv=0.0
        ))

        # outputs
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_STREAMS, self.tr("Mạng sông (line)"),
                                                            type=QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterRasterDestination(self.OUT_ACC, self.tr("Raster tích luỹ")))
        self.addParameter(QgsProcessingParameterRasterDestination(self.OUT_STREAM_R, self.tr("Raster sông (0/1)")))
        self.addParameter(QgsProcessingParameterRasterDestination(self.OUT_ORDER_R, self.tr("Raster cấp bậc (Strahler)")))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUT_BASINS, self.tr("Lưu vực (polygon, tuỳ chọn)"),
                                                            type=QgsProcessing.TypeVectorPolygon, optional=True))

    # ---- helpers: provider & algorithm discovery ----
    def _has(self, alg_id: str) -> bool:
        try:
            return QgsApplication.processingRegistry().algorithmById(alg_id) is not None
        except Exception:
            return False

    def _has_provider(self, prov_id: str) -> bool:
        return any(p.id().lower() == prov_id.lower()
                   for p in QgsApplication.processingRegistry().providers())

    def _has_contains(self, provider_id: str, substrings):
        prov = None
        for p in QgsApplication.processingRegistry().providers():
            if p.id().lower() == provider_id.lower():
                prov = p
                break
        if not prov:
            return []
        wanted = []
        subs = [s.lower().replace(" ", "") for s in substrings]
        for alg in prov.algorithms():
            aid = alg.id().lower().replace(" ", "")
            if all(s in aid for s in subs):
                wanted.append(alg.id())
        return wanted

    def _pick_alg(self, provider_id: str, candidates_exact: list, candidates_contains: list):
        for a in candidates_exact:
            if self._has(a):
                return a
        for cond in candidates_contains:
            matches = self._has_contains(provider_id, cond)
            if matches:
                return matches[0]
        return None

    def _first_raster_from(self, res: dict, prefer_keys=None):
        if not res:
            return None
        if prefer_keys:
            for k in prefer_keys:
                if k in res and isinstance(res[k], str) and res[k]:
                    return res[k]
        for v in res.values():
            if isinstance(v, str) and v:
                return v
        return None

    def _to_vlayer(self, src):
        if isinstance(src, QgsVectorLayer): return src
        v = QgsVectorLayer(src, "tmp", "ogr")
        return v if v.isValid() else None

    def _try_run(self, alg_id, variants, context, feedback):
        last_err = None
        for params in variants:
            try:
                return processing.run(alg_id, params, context=context, feedback=feedback)
            except Exception as e:
                last_err = e
                continue
        if last_err:
            raise last_err
        raise QgsProcessingException(f"Không thể chạy {alg_id} với các bộ tham số đã thử.")

    def _postprocess_streams(self, vect_in, do_smooth, method_idx, tol, iters, min_len, context, feedback):
        vlyr = self._to_vlayer(vect_in)
        if not vlyr:
            raise QgsProcessingException("Không đọc được vector stream trung gian.")
        current = vlyr.source()

        if do_smooth:
            if method_idx == 0:
                # Simplify: Douglas–Peucker
                simp = processing.run("native:simplifygeometries", {
                    "INPUT": current, "METHOD": 0, "TOLERANCE": float(tol),
                    "FETCH_GEOMETRY": False, "OUTPUT": "TEMPORARY_OUTPUT"
                }, context=context, feedback=feedback)
                current = simp["OUTPUT"]
            else:
                # Chaikin: đảm bảo OFFSET > 0 để tránh lỗi
                off = float(tol) if tol is not None else 0.0
                if off <= 0:
                    off = 0.01
                if off > 1e9:
                    off = 1e9
                # Dùng native:chaikinsmoothing (ổn định giữa các phiên bản)
                sm = processing.run("native:chaikinsmoothing", {
                    "INPUT": current, "ITERATIONS": int(max(1, iters)),
                    "OFFSET": float(off), "OUTPUT": "TEMPORARY_OUTPUT"
                }, context=context, feedback=feedback)
                current = sm["OUTPUT"]

        if (min_len or 0) > 0:
            flt = processing.run("native:extractbyexpression", {
                "INPUT": current, "EXPRESSION": f"$length >= {float(min_len)}",
                "OUTPUT": "TEMPORARY_OUTPUT"
            }, context=context, feedback=feedback)
            current = flt["OUTPUT"]

        return current

    def _copy_vector_to_sink(self, src_layer, sink_key, parameters, context, feedback):
        v = self._to_vlayer(src_layer)
        if not v: raise QgsProcessingException("Không đọc được lớp vector trung gian để ghi ra OUTPUT.")
        fields = v.fields(); wkb = v.wkbType(); crs = v.crs()
        sink, sink_id = self.parameterAsSink(parameters, sink_key, context, fields, wkb, crs)
        if sink is None: raise QgsProcessingException("Không tạo được FeatureSink cho đầu ra vector.")
        for f in v.getFeatures(): sink.addFeature(f)
        return sink_id

    def _raster_to_lines(self, raster_mask, context, feedback):
        """
        Chuyển raster mask (0/1) sang line vector (QGIS 3.16 an toàn).
        Thứ tự ưu tiên:
        1) native:pixeltolines (nếu có)
        2) GRASS r.to.vect type=line (kiểm tra hình học)
        3) GDAL polygonize -> native:polygonstolines
        """
        # 1) QGIS >= 3.22
        if self._has("native:pixeltolines"):
            return processing.run("native:pixeltolines", {
                "INPUT_RASTER": raster_mask, "RASTER_BAND": 1, "VALUES": "1",
                "FIELD_NAME": "val", "EIGHT_CONNECTEDNESS": False,
                "OUTPUT": "TEMPORARY_OUTPUT"
            }, context=context, feedback=feedback)["OUTPUT"]

        # Chuẩn hoá sang Byte 0/1 để GRASS hiểu đúng
        mask_byte = processing.run("gdal:translate", {
            "INPUT": raster_mask, "TARGET_CRS": None, "NODATA": 0,
            "COPY_SUBDATASETS": False, "OPTIONS": "", "EXTRA": "",
            "DATA_TYPE": 1,  # Byte
            "OUTPUT": "TEMPORARY_OUTPUT"
        }, context=context, feedback=feedback)["OUTPUT"]

        # 2) GRASS r.to.vect — thử type và chỉ nhận khi thực sự ra Line
        if self._has("grass7:r.to.vect"):
            for t in (2, 1, 0):
                try:
                    outv = processing.run("grass7:r.to.vect", {
                        "input": mask_byte,
                        "type": t,
                        "output": "TEMPORARY_OUTPUT",
                        "GRASS_REGION_PARAMETER": None,
                        "GRASS_REGION_CELLSIZE_PARAMETER": 0,
                        "GRASS_VECTOR_DSCO": "", "GRASS_VECTOR_LCO": "",
                        "GRASS_RASTER_FORMAT_OPT": "", "GRASS_RASTER_FORMAT_META": ""
                    }, context=context, feedback=feedback)["output"]
                    vl = QgsVectorLayer(outv, "chk", "ogr")
                    if vl and vl.isValid() and vl.geometryType() == QgsWkbTypes.LineGeometry:
                        return outv
                except Exception:
                    continue
            feedback.reportError("GRASS r.to.vect không cho line hợp lệ, chuyển sang phương án polygonize…")

        # 3) Fallback: polygonize → lines
        poly = processing.run("gdal:polygonize", {
            "INPUT": mask_byte, "BAND": 1, "FIELD": "val",
            "EIGHT_CONNECTEDNESS": False, "EXTRA": "",
            "OUTPUT": "TEMPORARY_OUTPUT"
        }, context=context, feedback=feedback)["OUTPUT"]
        return processing.run("native:polygonstolines", {
            "INPUT": poly, "OUTPUT": "TEMPORARY_OUTPUT"
        }, context=context, feedback=feedback)["OUTPUT"]

    # ---- main ----
    def processAlgorithm(self, parameters, context, feedback):
        dem = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem is None: raise QgsProcessingException("Không đọc được DEM.")

        use_fill = self.parameterAsBoolean(parameters, self.FILL_SINKS, context)
        thresh = self.parameterAsInt(parameters, self.THRESH, context)
        make_basins = self.parameterAsBoolean(parameters, self.MAKE_BASINS, context)
        engine_idx = self.parameterAsEnum(parameters, self.ENGINE, context)
        order_min = max(0, self.parameterAsInt(parameters, self.ORDER_MIN, context))

        do_smooth = self.parameterAsBoolean(parameters, self.SMOOTH_ENABLE, context)
        method_idx = self.parameterAsEnum(parameters, self.SMOOTH_METHOD, context)
        tol = self.parameterAsDouble(parameters, self.SMOOTH_TOL, context)
        iters = self.parameterAsInt(parameters, self.SMOOTH_ITER, context)
        min_len = self.parameterAsDouble(parameters, self.MIN_LENGTH, context)

        out_acc = self.parameterAsOutputLayer(parameters, self.OUT_ACC, context)
        out_stream_r = self.parameterAsOutputLayer(parameters, self.OUT_STREAM_R, context)
        out_order_r = self.parameterAsOutputLayer(parameters, self.OUT_ORDER_R, context)

        backend = "AUTO"
        if engine_idx == 1: backend = "SAGA"
        elif engine_idx == 2: backend = "GRASS"
        elif engine_idx == 3: backend = "WBT"

        if backend == "AUTO":
            if self._has_provider("saga"):
                backend = "SAGA"
            elif self._has_provider("grass7"):
                backend = "GRASS"
            elif self._has_provider("wbt"):
                backend = "WBT"
            else:
                raise QgsProcessingException("Không thấy SAGA/GRASS/WBT trong Processing.")

        if backend == "SAGA":
            return self._run_saga(dem, use_fill, thresh, make_basins, order_min,
                                  out_acc, out_stream_r, out_order_r,
                                  parameters, context, feedback,
                                  do_smooth, method_idx, tol, iters, min_len)
        elif backend == "GRASS":
            return self._run_grass(dem, use_fill, thresh, make_basins, order_min,
                                   out_acc, out_stream_r, out_order_r,
                                   parameters, context, feedback,
                                   do_smooth, method_idx, tol, iters, min_len)
        else:
            return self._run_wbt(dem, use_fill, thresh, make_basins, order_min,
                                 out_acc, out_stream_r, out_order_r,
                                 parameters, context, feedback,
                                 do_smooth, method_idx, tol, iters, min_len)

    # ---- SAGA (primary) ----
    def _run_saga(self, dem, use_fill, thresh, make_basins, order_min,
                  out_acc, out_stream_r, out_order_r,
                  parameters, context, feedback,
                  do_smooth, method_idx, tol, iters, min_len):

        # Fill sinks
        fill_id = self._pick_alg("saga",
                                 ["saga:fillsinkswangliu", "saga:fillsinksplanchon"],
                                 [["fill","sinks","wang"], ["fill","sinks","planchon"]])
        dem_in = dem
        filled_res = None
        if use_fill and fill_id:
            feedback.pushInfo(f"SAGA: {fill_id}…")
            try:
                filled_res = self._try_run(fill_id, [
                    {"ELEV": dem_in, "FILLED": "TEMPORARY_OUTPUT",
                     "FDIR": "TEMPORARY_OUTPUT", "WSHED": "TEMPORARY_OUTPUT"},
                    {"ELEVATION": dem_in, "FILLED": "TEMPORARY_OUTPUT"}
                ], context, feedback)
                dem_in = filled_res.get("FILLED", dem_in)
            except Exception as e:
                feedback.reportError(f"Fill sinks lỗi ({e}), dùng DEM gốc.")

        # Flow accumulation
        acc_id = self._pick_alg("saga",
                                ["saga:flowaccumulationqmofesp","saga:flowaccumulationtopdown",
                                 "saga:flowaccumulationparallel","saga:flowaccumulation"],
                                [["flow","accumulation","qm"],["flow","accumulation","top"],
                                 ["flow","accumulation","parallel"],["flow","accumulation"]])
        if not acc_id:
            raise QgsProcessingException("Không tìm thấy SAGA Flow Accumulation.")
        feedback.pushInfo(f"SAGA: {acc_id}…")
        if "qmofesp" in acc_id.lower():
            fa = self._try_run(acc_id, [
                {"DEM": dem_in, "FLOW": "TEMPORARY_OUTPUT"},
                {"ELEVATION": dem_in, "FLOW": "TEMPORARY_OUTPUT"}
            ], context, feedback)
            acc_r = fa.get("FLOW")
        else:
            fa = self._try_run(acc_id, [
                {"ELEVATION": dem_in, "ACCU": "TEMPORARY_OUTPUT"},
                {"ELEV": dem_in, "ACCU": "TEMPORARY_OUTPUT"},
                {"DEM": dem_in, "ACCU": "TEMPORARY_OUTPUT"}
            ], context, feedback)
            acc_r = fa.get("ACCU") or self._first_raster_from(fa, ["ACCU","FLOW","AREA","SCA"])
        if not acc_r:
            raise QgsProcessingException("Không lấy được raster tích luỹ từ SAGA.")

        # Channel Network
        chn_id = self._pick_alg("saga", ["saga:channelnetwork"], [["channel","network"]])
        if not chn_id:
            raise QgsProcessingException("Không tìm thấy SAGA Channel Network.")
        feedback.pushInfo(f"SAGA: {chn_id}…")
        ch = self._try_run(chn_id, [{
            "ELEVATION": dem_in, "INIT_GRID": acc_r, "INIT_METHOD": 2,
            "INIT_VALUE": float(thresh), "CHNLNTWRK": "TEMPORARY_OUTPUT",
            "CHNLROUTE": "TEMPORARY_OUTPUT", "SHAPES": "TEMPORARY_OUTPUT"
        }], context, feedback)

        stream_r_grid = ch.get("CHNLNTWRK") or ch.get("STREAM") or self._first_raster_from(ch, ["CHNLNTWRK","STREAM"])
        stream_v_any = ch.get("SHAPES")

        # Strahler Order (raster)
        ord_id = self._pick_alg("saga", ["saga:strahlerorder"], [["strahler","order"]])
        if not ord_id:
            raise QgsProcessingException("Không tìm thấy SAGA Strahler Order.")
        feedback.pushInfo("SAGA: strahlerorder…")
        so = self._try_run(ord_id, [
            {"DEM": dem_in, "STRAHLER": "TEMPORARY_OUTPUT"},
            {"ELEVATION": dem_in, "STRAHLER": "TEMPORARY_OUTPUT"}
        ], context, feedback)
        order_r_grid = so.get("STRAHLER")

        # Xuất raster chuẩn
        acc_tif = processing.run("gdal:translate", {
            "INPUT": acc_r, "TARGET_CRS": None, "NODATA": None,
            "COPY_SUBDATASETS": False, "OPTIONS": "", "EXTRA": "",
            "DATA_TYPE": 0, "OUTPUT": out_acc
        }, context=context, feedback=feedback)["OUTPUT"]

        stream_tif = processing.run("gdal:translate", {
            "INPUT": stream_r_grid, "TARGET_CRS": None, "NODATA": 0,
            "COPY_SUBDATASETS": False, "OPTIONS": "", "EXTRA": "",
            "DATA_TYPE": 1, "OUTPUT": out_stream_r
        }, context=context, feedback=feedback)["OUTPUT"]

        order_tif = processing.run("gdal:translate", {
            "INPUT": order_r_grid, "TARGET_CRS": None, "NODATA": 0,
            "COPY_SUBDATASETS": False, "OPTIONS": "", "EXTRA": "",
            "DATA_TYPE": 1, "OUTPUT": out_order_r
        }, context=context, feedback=feedback)["OUTPUT"]

        # Lọc theo ORDER_MIN rồi vector hoá
        mask = processing.run("gdal:rastercalculator", {
            "INPUT_A": order_tif, "BAND_A": 1,
            "INPUT_B": stream_tif, "BAND_B": 1,
            "FORMULA": f"(A>={int(order_min)})*(B>0)",
            "RTYPE": 1,  # Byte
            "EXTRA": "", "OPTIONS": "", "NO_DATA": 0,
            "OUTPUT": "TEMPORARY_OUTPUT"
        }, context=context, feedback=feedback)["OUTPUT"]

        vec_mask = self._raster_to_lines(mask, context, feedback)

        final_tmp = self._postprocess_streams(vec_mask, do_smooth, method_idx, tol, iters, min_len, context, feedback)
        streams_sink_id = self._copy_vector_to_sink(final_tmp, self.OUT_STREAMS, parameters, context, feedback)

        # Basins (tuỳ chọn)
        basins_sink_id = None
        if make_basins and filled_res and filled_res.get("WSHED"):
            bas_vec = processing.run("gdal:polygonize", {
                "INPUT": filled_res.get("WSHED"), "BAND": 1, "FIELD": "id",
                "EIGHT_CONNECTEDNESS": False, "EXTRA": "",
                "OUTPUT": "TEMPORARY_OUTPUT"
            }, context=context, feedback=feedback)["OUTPUT"]
            basins_sink_id = self._copy_vector_to_sink(bas_vec, self.OUT_BASINS, parameters, context, feedback)

        return {
            self.OUT_STREAMS: streams_sink_id,
            self.OUT_ACC: acc_tif,
            self.OUT_STREAM_R: stream_tif,
            self.OUT_ORDER_R: order_tif,
            self.OUT_BASINS: basins_sink_id
        }

    # ---- GRASS (fallback) ----
    def _run_grass(self, dem, use_fill, thresh, make_basins, order_min,
                   out_acc, out_stream_r, out_order_r,
                   parameters, context, feedback,
                   do_smooth, method_idx, tol, iters, min_len):

        dem_in = dem
        if use_fill and self._has("grass7:r.fill.dir"):
            feedback.pushInfo("GRASS: r.fill.dir…")
            rfill = processing.run("grass7:r.fill.dir", {
                "input": dem_in, "output": "TEMPORARY_OUTPUT",
                "direction": "TEMPORARY_OUTPUT", "format": 0,
                "GRASS_REGION_PARAMETER": None, "GRASS_REGION_CELLSIZE_PARAMETER": 0,
                "GRASS_RASTER_FORMAT_OPT": "", "GRASS_RASTER_FORMAT_META": ""
            }, context=context, feedback=feedback)
            dem_in = rfill["output"]

        feedback.pushInfo("GRASS: r.watershed…")
        ws = processing.run("grass7:r.watershed", {
            "elevation": dem_in, "accumulation": "TEMPORARY_OUTPUT", "drainage": "TEMPORARY_OUTPUT",
            "convergence": 5, "memory": 300, "threshold": 0,
            "GRASS_REGION_PARAMETER": None, "GRASS_REGION_CELLSIZE_PARAMETER": 0,
            "GRASS_RASTER_FORMAT_OPT": "", "GRASS_RASTER_FORMAT_META": ""
        }, context=context, feedback=feedback)
        acc_r = ws["accumulation"]; dir_r = ws["drainage"]

        feedback.pushInfo("GRASS: r.stream.extract…")
        st = processing.run("grass7:r.stream.extract", {
            "elevation": dem_in, "accumulation": acc_r, "threshold": int(thresh),
            "d8cut": 999999, "mexp": 0, "stream_raster": "TEMPORARY_OUTPUT", "direction": dir_r,
            "GRASS_REGION_PARAMETER": None, "GRASS_REGION_CELLSIZE_PARAMETER": 0,
            "GRASS_RASTER_FORMAT_OPT": "", "GRASS_RASTER_FORMAT_META": ""
        }, context=context, feedback=feedback)
        stream_r = st["stream_raster"]

        feedback.pushInfo("GRASS: r.stream.order…")
        ro = processing.run("grass7:r.stream.order", {
            "stream_rast": stream_r, "direction": dir_r, "accumulation": acc_r,
            "network": "TEMPORARY_OUTPUT", "stream_vect": "TEMPORARY_OUTPUT", "order": "TEMPORARY_OUTPUT",
            "method": 1,  # Strahler raster
            "GRASS_REGION_PARAMETER": None, "GRASS_REGION_CELLSIZE_PARAMETER": 0,
            "GRASS_VECTOR_DSCO": "", "GRASS_VECTOR_LCO": "",
            "GRASS_RASTER_FORMAT_OPT": "", "GRASS_RASTER_FORMAT_META": ""
        }, context=context, feedback=feedback)
        order_r = ro["order"]

        # write rasters
        acc_tif = processing.run("gdal:translate", {
            "INPUT": acc_r, "TARGET_CRS": None, "NODATA": None,
            "COPY_SUBDATASETS": False, "OPTIONS": "", "EXTRA": "",
            "DATA_TYPE": 0, "OUTPUT": out_acc
        }, context=context, feedback=feedback)["OUTPUT"]

        stream_tif = processing.run("gdal:translate", {
            "INPUT": stream_r, "TARGET_CRS": None, "NODATA": 0,
            "COPY_SUBDATASETS": False, "OPTIONS": "", "EXTRA": "",
            "DATA_TYPE": 1, "OUTPUT": out_stream_r
        }, context=context, feedback=feedback)["OUTPUT"]

        order_tif = processing.run("gdal:translate", {
            "INPUT": order_r, "TARGET_CRS": None, "NODATA": 0,
            "COPY_SUBDATASETS": False, "OPTIONS": "", "EXTRA": "",
            "DATA_TYPE": 1, "OUTPUT": out_order_r
        }, context=context, feedback=feedback)["OUTPUT"]

        # mask theo ORDER_MIN → lines
        mask = processing.run("gdal:rastercalculator", {
            "INPUT_A": order_tif, "BAND_A": 1,
            "INPUT_B": stream_tif, "BAND_B": 1,
            "FORMULA": f"(A>={int(order_min)})*(B>0)",
            "RTYPE": 1, "EXTRA": "", "OPTIONS": "", "NO_DATA": 0,
            "OUTPUT": "TEMPORARY_OUTPUT"
        }, context=context, feedback=feedback)["OUTPUT"]

        vec_mask = self._raster_to_lines(mask, context, feedback)

        final_tmp = self._postprocess_streams(vec_mask, do_smooth, method_idx, tol, iters, min_len, context, feedback)
        streams_sink_id = self._copy_vector_to_sink(final_tmp, self.OUT_STREAMS, parameters, context, feedback)

        basins_sink_id = None
        if make_basins and self._has("grass7:r.stream.basins"):
            bas = processing.run("grass7:r.stream.basins", {
                "direction": dir_r, "streams": stream_r, "basins": "TEMPORARY_OUTPUT",
                "GRASS_REGION_PARAMETER": None, "GRASS_REGION_CELLSIZE_PARAMETER": 0,
                "GRASS_RASTER_FORMAT_OPT": "", "GRASS_RASTER_FORMAT_META": ""
            }, context=context, feedback=feedback)
            bas_vec = processing.run("gdal:polygonize", {
                "INPUT": bas["basins"], "BAND": 1, "FIELD": "id",
                "EIGHT_CONNECTEDNESS": False, "EXTRA": "", "OUTPUT": "TEMPORARY_OUTPUT"
            }, context=context, feedback=feedback)["OUTPUT"]
            basins_sink_id = self._copy_vector_to_sink(bas_vec, self.OUT_BASINS, parameters, context, feedback)

        return {
            self.OUT_STREAMS: streams_sink_id,
            self.OUT_ACC: acc_tif,
            self.OUT_STREAM_R: stream_tif,
            self.OUT_ORDER_R: order_tif,
            self.OUT_BASINS: basins_sink_id
        }

    # ---- WhiteboxTools (fallback) ----
    def _run_wbt(self, dem, use_fill, thresh, make_basins, order_min,
                 out_acc, out_stream_r, out_order_r,
                 parameters, context, feedback,
                 do_smooth, method_idx, tol, iters, min_len):

        if not self._has_provider("wbt"):
            raise QgsProcessingException("WhiteboxTools không sẵn có.")

        dem_in = dem
        if use_fill:
            feedback.pushInfo("WBT: breachdepressionsleastcost…")
            br = processing.run("wbt:breachdepressionsleastcost", {
                "dem": dem_in, "out_dem": "TEMPORARY_OUTPUT"
            }, context=context, feedback=feedback)
            dem_in = br["out_dem"]

        feedback.pushInfo("WBT: d8flowaccumulation…")
        fa = processing.run("wbt:d8flowaccumulation", {
            "i": dem_in, "out_type": 0, "dn": None, "esri_pntr": False,
            "log": False, "clip": False, "o": "TEMPORARY_OUTPUT"
        }, context=context, feedback=feedback)
        acc_r = fa["o"]

        feedback.pushInfo("WBT: extractstreams…")
        ex = processing.run("wbt:extractstreams", {
            "flow_accum": acc_r, "threshold": float(thresh),
            "zero_background": True, "streams": "TEMPORARY_OUTPUT"
        }, context=context, feedback=feedback)
        stream_r = ex["streams"]

        feedback.pushInfo("WBT: strahlerorder…")
        so = processing.run("wbt:strahlerorder", {
            "d8_pntr": None, "streams": stream_r, "output": "TEMPORARY_OUTPUT"
        }, context=context, feedback=feedback)
        order_r = so["output"]

        acc_tif = processing.run("gdal:translate", {
            "INPUT": acc_r, "TARGET_CRS": None, "NODATA": None,
            "COPY_SUBDATASETS": False, "OPTIONS": "", "EXTRA": "",
            "DATA_TYPE": 0, "OUTPUT": out_acc
        }, context=context, feedback=feedback)["OUTPUT"]
        stream_tif = processing.run("gdal:translate", {
            "INPUT": stream_r, "TARGET_CRS": None, "NODATA": 0,
            "COPY_SUBDATASETS": False, "OPTIONS": "", "EXTRA": "",
            "DATA_TYPE": 1, "OUTPUT": out_stream_r
        }, context=context, feedback=feedback)["OUTPUT"]
        order_tif = processing.run("gdal:translate", {
            "INPUT": order_r, "TARGET_CRS": None, "NODATA": 0,
            "COPY_SUBDATASETS": False, "OPTIONS": "", "EXTRA": "",
            "DATA_TYPE": 1, "OUTPUT": out_order_r
        }, context=context, feedback=feedback)["OUTPUT"]

        mask = processing.run("gdal:rastercalculator", {
            "INPUT_A": order_tif, "BAND_A": 1,
            "INPUT_B": stream_tif, "BAND_B": 1,
            "FORMULA": f"(A>={int(order_min)})*(B>0)",
            "RTYPE": 1, "EXTRA": "", "OPTIONS": "", "NO_DATA": 0,
            "OUTPUT": "TEMPORARY_OUTPUT"
        }, context=context, feedback=feedback)["OUTPUT"]

        stream_v = self._raster_to_lines(mask, context, feedback)

        final_tmp = self._postprocess_streams(stream_v, do_smooth, method_idx, tol, iters, min_len, context, feedback)
        streams_sink_id = self._copy_vector_to_sink(final_tmp, self.OUT_STREAMS, parameters, context, feedback)

        return {
            self.OUT_STREAMS: streams_sink_id,
            self.OUT_ACC: acc_tif,
            self.OUT_STREAM_R: stream_tif,
            self.OUT_ORDER_R: order_tif,
            self.OUT_BASINS: None
        }
