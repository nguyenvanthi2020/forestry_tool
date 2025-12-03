# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsApplication,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorDestination,
    QgsProcessingParameterVectorLayer,
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsWkbTypes
)
import math
import processing


class WatershedFromDEM(QgsProcessingAlgorithm):
    # ---- keys
    P_DEM = 'DEM'
    P_FILL = 'FILL_SINKS'
    P_POUR_LAYER = 'POUR_LAYER'
    P_POUR_X = 'POUR_X'
    P_POUR_Y = 'POUR_Y'
    P_POUR_CRS = 'POUR_CRS'
    P_SNAP_ENABLE = 'SNAP_ENABLE'
    P_AUTO_THRESH = 'AUTO_THRESHOLD'          # << NEW
    P_SNAP_ACC_THRESH = 'SNAP_ACC_THRESH'
    P_SNAP_RADIUS = 'SNAP_RADIUS'
    P_AOI = 'AOI'
    P_SMOOTH_EN = 'SMOOTH_ENABLE'
    P_SMOOTH_IT = 'SMOOTH_ITERS'
    P_SIMP_EN = 'SIMPLIFY_ENABLE'
    P_SIMP_TOL = 'SIMPLIFY_TOL'
    P_OUTPUT = 'OUTPUT'

    # ---- metadata
    def name(self): return 'watershed_from_dem'
    def displayName(self): return self.tr('Khoanh vẽ ranh giới lưu vực từ DEM')
    def group(self): return self.tr('Tiện ích Raster')
    def groupId(self): return 'raster_utils'
    def createInstance(self): return WatershedFromDEM()
    def tr(self, t): return QCoreApplication.translate('WatershedFromDEM', t)

    def shortHelpString(self):
        return self.tr(
            "Khoanh vẽ lưu vực từ DEM + pour point (layer hoặc X/Y + CRS). "
            "Fill sinks (SAGA→GRASS→WBT), Snap theo Flow Accumulation (ngưỡng & bán kính). "
            "Tùy chọn 'Auto threshold' tự suy ra ngưỡng tích luỹ (cells) ≈ 1 km² dựa trên kích thước ô DEM. "
            "Có AOI clip, Smooth & Simplify. Tương thích QGIS 3.16+."
        )

    # ---- UI parameters
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.P_DEM, self.tr('DEM (raster)')))
        self.addParameter(QgsProcessingParameterBoolean(self.P_FILL, self.tr('Fill sinks DEM (khuyến nghị)'), True))

        self.addParameter(QgsProcessingParameterFeatureSource(
            self.P_POUR_LAYER, self.tr('Pour point (lớp điểm) — để trống nếu dùng X/Y'),
            types=[QgsProcessing.TypeVectorPoint], optional=True
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.P_POUR_X, self.tr('Pour point X (bỏ trống nếu dùng lớp điểm)'),
            type=QgsProcessingParameterNumber.Double, optional=True
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.P_POUR_Y, self.tr('Pour point Y (bỏ trống nếu dùng lớp điểm)'),
            type=QgsProcessingParameterNumber.Double, optional=True
        ))
        self.addParameter(QgsProcessingParameterCrs(
            self.P_POUR_CRS, self.tr('CRS của toạ độ X/Y (nếu dùng X/Y)'), defaultValue='EPSG:4326'
        ))

        self.addParameter(QgsProcessingParameterBoolean(self.P_SNAP_ENABLE, self.tr('Snap pour point theo Flow Accumulation'), False))
        # NEW: auto threshold toggle (mặc định BẬT)
        self.addParameter(QgsProcessingParameterBoolean(self.P_AUTO_THRESH, self.tr('Auto threshold (≈ 1 km², tính theo kích thước ô DEM)'), True))
        # Giữ tham số thủ công để người dùng override khi tắt Auto
        self.addParameter(QgsProcessingParameterNumber(
            self.P_SNAP_ACC_THRESH, self.tr('Ngưỡng tích luỹ (cells) khi tắt Auto'),
            type=QgsProcessingParameterNumber.Double, defaultValue=1000.0, minValue=1.0
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.P_SNAP_RADIUS, self.tr('Bán kính snap (đơn vị CRS DEM)'),
            type=QgsProcessingParameterNumber.Double, defaultValue=200.0, minValue=0.0
        ))

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.P_AOI, self.tr('AOI (cắt lưu vực theo đa giác này) — tuỳ chọn'),
            types=[QgsProcessing.TypeVectorPolygon], optional=True
        ))

        self.addParameter(QgsProcessingParameterBoolean(self.P_SMOOTH_EN, self.tr('Làm trơn (smooth) ranh giới'), False))
        self.addParameter(QgsProcessingParameterNumber(
            self.P_SMOOTH_IT, self.tr('Số vòng lặp smooth'),
            type=QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1
        ))
        self.addParameter(QgsProcessingParameterBoolean(self.P_SIMP_EN, self.tr('Đơn giản hoá (simplify) ranh giới'), False))
        self.addParameter(QgsProcessingParameterNumber(
            self.P_SIMP_TOL, self.tr('Sai số simplify (đơn vị CRS output)'),
            type=QgsProcessingParameterNumber.Double, defaultValue=10.0, minValue=0.0
        ))
        self.addParameter(QgsProcessingParameterVectorDestination(self.P_OUTPUT, self.tr('Ranh giới lưu vực (polygon)')))

    # ============ HELPERS ============
    def _has(self, alg_id: str) -> bool:
        try:
            return QgsApplication.processingRegistry().algorithmById(alg_id) is not None
        except Exception:
            return False

    def _has_contains(self, provider_id: str, substrings):
        prov = None
        for p in QgsApplication.processingRegistry().providers():
            if p.id().lower() == provider_id.lower():
                prov = p; break
        if not prov: return []
        subs = [s.lower().replace(" ", "") for s in substrings]
        out = []
        for alg in prov.algorithms():
            aid = alg.id().lower().replace(" ", "")
            if all(s in aid for s in subs):
                out.append(alg.id())
        return out

    def _pick_alg(self, provider_id: str, candidates_exact: list, candidates_contains: list):
        for a in candidates_exact:
            if self._has(a):
                return a
        for cond in candidates_contains:
            matches = self._has_contains(provider_id, cond)
            if matches:
                return matches[0]
        return None

    def _try_run(self, alg_id, param_variants, context, feedback):
        last_err = None
        for p in param_variants:
            try:
                return processing.run(alg_id, p, context=context, feedback=feedback)
            except Exception as e:
                last_err = e
        raise last_err if last_err else QgsProcessingException(self.tr(f'Không chạy được {alg_id}'))

    def _first_point_from_layer(self, src):
        for f in src.getFeatures(QgsFeatureRequest().setLimit(1)):
            g = f.geometry()
            if g and not g.isEmpty():
                if g.type() == QgsWkbTypes.PointGeometry:
                    try: return QgsPointXY(g.asPoint())
                    except Exception:
                        mp = g.asMultiPoint()
                        if mp: return QgsPointXY(mp[0])
        return None

    def _make_point_layer(self, point_xy: QgsPointXY, crs_authid: str):
        uri = f'Point?crs={crs_authid}&field=id:integer'
        vl = QgsVectorLayer(uri, 'pour_point_tmp', 'memory')
        pr = vl.dataProvider()
        pr.addAttributes([QgsField('id', QVariant.Int)])
        vl.updateFields()
        feat = QgsFeature(vl.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(point_xy))
        feat['id'] = 1
        pr.addFeature(feat)
        vl.updateExtents()
        return vl

    def _as_raster_src(self, obj):
        if isinstance(obj, str):
            return obj
        try:
            return obj.source()
        except Exception:
            return obj

    def _to_rlayer(self, src, name='tmp_ras'):
        if isinstance(src, QgsRasterLayer) and src.isValid():
            return src
        if isinstance(src, str):
            rl = QgsRasterLayer(src, name, 'gdal')
            if not rl.isValid():
                raise QgsProcessingException(self.tr(f'Không load được raster: {src}'))
            return rl
        try:
            path = src.source()
            rl = QgsRasterLayer(path, name, 'gdal')
            if not rl.isValid():
                raise QgsProcessingException(self.tr(f'Không load được raster: {path}'))
            return rl
        except Exception:
            raise QgsProcessingException(self.tr('Không chuyển được raster sang QgsRasterLayer.'))

    def _estimate_pixel_size_m(self, dem_layer):
        """
        Trả về (px_m, py_m) ước lượng theo mét.
        - Nếu CRS phẳng (projected): dùng trực tiếp rasterUnitsPerPixelX/Y.
        - Nếu CRS địa lý (độ): quy đổi theo vĩ độ tâm extent.
        """
        px = dem_layer.rasterUnitsPerPixelX()
        py = dem_layer.rasterUnitsPerPixelY()
        if not dem_layer.crs().isGeographic():
            return abs(px), abs(py)

        # Quy đổi độ -> mét theo vĩ độ tâm
        center = dem_layer.extent().center()
        lat = center.y()
        # gần đúng: 1° lat ≈ 110540 m; 1° lon ≈ 111320 * cos(lat)
        meters_per_deg_y = 110540.0
        meters_per_deg_x = 111320.0 * math.cos(math.radians(lat))
        px_m = abs(px) * abs(meters_per_deg_x)
        py_m = abs(py) * abs(meters_per_deg_y)
        return px_m, py_m

    # -------- FILL SINKS: SAGA → GRASS → WBT --------
    def _fill_sinks_resilient(self, dem_src, context, feedback):
        dem_src = self._as_raster_src(dem_src)

        saga_fill_id = self._pick_alg("saga",
            ["saga:fillsinkswangliu", "saga:fillsinksplanchon", "saga:fillsinks"],
            [["fill","sinks","wang"], ["fill","sinks","planchon"], ["fill","sinks"]]
        )
        if saga_fill_id:
            feedback.pushInfo(self.tr(f"SAGA: {saga_fill_id} …"))
            try:
                res = self._try_run(saga_fill_id, [
                    {"ELEV": dem_src, "FILLED":"TEMPORARY_OUTPUT",
                     "FDIR":"TEMPORARY_OUTPUT", "WSHED":"TEMPORARY_OUTPUT"},
                    {"ELEV": dem_src, "FILLED":"TEMPORARY_OUTPUT"},
                    {"ELEVATION": dem_src, "FILLED":"TEMPORARY_OUTPUT"},
                    {"DEM": dem_src, "FILLED":"TEMPORARY_OUTPUT"}
                ], context, feedback)
                return self._as_raster_src(res.get("FILLED") or res.get("ELEVATION") or res.get("ELEV") or dem_src)
            except Exception as e:
                feedback.reportError(self.tr(f"SAGA Fill sinks lỗi: {e}"))

        if self._has("grass7:r.fill.dir"):
            feedback.pushInfo(self.tr("GRASS: r.fill.dir (ép areas='') …"))
            try:
                gres = processing.run("grass7:r.fill.dir", {
                    "input": dem_src, "type": 0, "format": 0,
                    "areas": "",
                    "output": "TEMPORARY_OUTPUT", "direction": "TEMPORARY_OUTPUT",
                    "GRASS_REGION_PARAMETER": None, "GRASS_REGION_CELLSIZE_PARAMETER": 0,
                    "GRASS_RASTER_FORMAT_META": "", "GRASS_RASTER_FORMAT_OPT": ""
                }, context=context, feedback=feedback)
                return self._as_raster_src(gres["output"])
            except Exception as e:
                feedback.reportError(self.tr(f"GRASS r.fill.dir lỗi: {e}"))

        if self._has("wbt:breachdepressionsleastcost"):
            feedback.pushInfo(self.tr("WBT: breachdepressionsleastcost …"))
            try:
                wres = processing.run("wbt:breachdepressionsleastcost", {
                    "dem": dem_src, "out_dem": "TEMPORARY_OUTPUT"
                }, context=context, feedback=feedback)
                return self._as_raster_src(wres["out_dem"])
            except Exception as e:
                feedback.reportError(self.tr(f"WBT breachdepressionsleastcost lỗi: {e}"))

        feedback.pushWarning(self.tr("Không fill sinks được bằng SAGA/GRASS/WBT — dùng DEM gốc."))
        return dem_src

    # -------- FLOW ACCUMULATION --------
    def _flow_accumulation_resilient(self, dem_src, context, feedback):
        dem_src = self._as_raster_src(dem_src)

        candidates = [
            ("saga:flowaccumulationtopdown",
             [{"ELEVATION": dem_src, "FLOW":"TEMPORARY_OUTPUT"},
              {"ELEV": dem_src, "FLOW":"TEMPORARY_OUTPUT"},
              {"DEM": dem_src, "FLOW":"TEMPORARY_OUTPUT"}],
             ["FLOW"]),
            ("saga:flowaccumulation(mfd)",
             [{"ELEVATION": dem_src, "FLOW":"TEMPORARY_OUTPUT"},
              {"ELEV": dem_src, "FLOW":"TEMPORARY_OUTPUT"}],
             ["FLOW"]),
            ("saga:flowaccumulation",
             [{"ELEVATION": dem_src, "FLOW":"TEMPORARY_OUTPUT"},
              {"ELEV": dem_src, "FLOW":"TEMPORARY_OUTPUT"}],
             ["FLOW","AREA","ACCU"])
        ]
        for alg_id, params, outkeys in candidates:
            if self._has(alg_id):
                feedback.pushInfo(self.tr(f"SAGA Flow Accumulation: {alg_id} …"))
                try:
                    res = self._try_run(alg_id, params, context, feedback)
                    for k in outkeys:
                        if res.get(k):
                            return self._as_raster_src(res.get(k))
                except Exception as e:
                    feedback.reportError(self.tr(f"{alg_id} lỗi: {e}"))

        if self._has("wbt:d8flowaccumulation"):
            feedback.pushInfo(self.tr("WBT: d8flowaccumulation …"))
            try:
                wres = processing.run("wbt:d8flowaccumulation", {
                    "dem": dem_src, "out_accum": "TEMPORARY_OUTPUT", "out_type": 0
                }, context=context, feedback=feedback)
                return self._as_raster_src(wres["out_accum"])
            except Exception as e:
                feedback.reportError(self.tr(f"WBT d8flowaccumulation lỗi: {e}"))

        if self._has("grass7:r.watershed"):
            feedback.pushInfo(self.tr("GRASS: r.watershed (accumulation) …"))
            try:
                gres = processing.run("grass7:r.watershed", {
                    "elevation": dem_src,
                    "threshold": 1,
                    "accumulation": "TEMPORARY_OUTPUT",
                    "drainage": "TEMPORARY_OUTPUT",
                    "GRASS_REGION_PARAMETER": None,
                    "GRASS_REGION_CELLSIZE_PARAMETER": 0,
                    "GRASS_RASTER_FORMAT_META": "",
                    "GRASS_RASTER_FORMAT_OPT": ""
                }, context=context, feedback=feedback)
                return self._as_raster_src(gres["accumulation"])
            except Exception as e:
                feedback.reportError(self.tr(f"GRASS r.watershed lỗi: {e}"))

        raise QgsProcessingException(self.tr("Không tính được Flow Accumulation; kiểm tra SAGA/WBT/GRASS."))

    # -------- SNAP pour point to stream lines --------
    def _snap_point_to_stream(self, pt_layer_dem_crs, acc_src, acc_thresh, radius, dem_crs_authid, context, feedback):
        acc_layer = self._to_rlayer(acc_src, 'accumulation')

        # 1) mask acc >= threshold
        feedback.pushInfo(self.tr(f"Tạo mask dòng chảy từ Flow Accumulation (threshold = {acc_thresh} cells)…"))
        mask_res = processing.run('qgis:rastercalculator', {
            'EXPRESSION': f'("{acc_layer.name()}@1" >= {acc_thresh})*1',
            'LAYERS': [acc_layer],
            'CRS': dem_crs_authid,
            'EXTENT': acc_layer.extent(),
            'OUTPUT': 'TEMPORARY_OUTPUT',
            'OUTPUT_DATATYPE': 5,
            'CELLSIZE': 0
        }, context=context, feedback=feedback)
        stream_mask = mask_res['OUTPUT']

        # 2) mask → polys → lines
        poly_res = processing.run('gdal:polygonize', {
            'INPUT': stream_mask, 'BAND':1, 'FIELD':'DN',
            'EIGHT_CONNECTEDNESS': False, 'EXTRA':'', 'OUTPUT':'TEMPORARY_OUTPUT'
        }, context=context, feedback=feedback)
        stream_polys = poly_res['OUTPUT']

        lines_res = processing.run('native:polygonstolines', {
            'INPUT': stream_polys, 'OUTPUT':'TEMPORARY_OUTPUT'
        }, context=context, feedback=feedback)
        stream_lines = lines_res['OUTPUT']

        # 3) snap
        feedback.pushInfo(self.tr("Snap pour point vào dòng chảy…"))
        snap_res = processing.run('native:snapgeometries', {
            'INPUT': pt_layer_dem_crs,
            'REFERENCE_LAYER': stream_lines,
            'TOLERANCE': radius,
            'BEHAVIOR': 0,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }, context=context, feedback=feedback)
        snapped = snap_res['OUTPUT']

        vl = snapped if isinstance(snapped, QgsVectorLayer) else QgsVectorLayer(snapped, 'snapped_pt', 'ogr')
        for f in vl.getFeatures(QgsFeatureRequest().setLimit(1)):
            g = f.geometry()
            if g and not g.isEmpty():
                p = g.asPoint()
                return QgsPointXY(p)
        raise QgsProcessingException(self.tr("Không snap được pour point (kiểm tra ngưỡng tích luỹ & bán kính)."))

    # -------- SAGA Upslope Area from X/Y (ưu tiên SAGA, có METHOD) --------
    def _saga_upslope_area_from_point(self, dem_src, pt_xy, context, feedback):
        dem_src = self._as_raster_src(dem_src)
        x, y = pt_xy.x(), pt_xy.y()
        method_vals = [0, 1, 2, 3, 4, 5]

        variants = []
        for m in method_vals:
            variants.append(('saga:upslopearea', [
                {'ELEVATION': dem_src, 'TARGET_PT_X': x, 'TARGET_PT_Y': y, 'METHOD': m, 'FLOW': 0, 'AREA':'TEMPORARY_OUTPUT'},
                {'ELEV': dem_src,     'TARGET_PT_X': x, 'TARGET_PT_Y': y, 'METHOD': m, 'FLOW': 0, 'AREA':'TEMPORARY_OUTPUT'},
                {'DEM': dem_src,      'TARGET_PT_X': x, 'TARGET_PT_Y': y, 'METHOD': m, 'FLOW': 0, 'AREA':'TEMPORARY_OUTPUT'},
            ]))
            variants.append(('saga:upslopearea', [
                {'ELEVATION': dem_src, 'X': x, 'Y': y, 'METHOD': m, 'FLOW': 0, 'AREA':'TEMPORARY_OUTPUT'},
                {'ELEV': dem_src,      'X': x, 'Y': y, 'METHOD': m, 'FLOW': 0, 'AREA':'TEMPORARY_OUTPUT'},
            ]))

        variants.append(('saga:upslopeareafrompoint', [
            {'ELEVATION': dem_src, 'X': x, 'Y': y, 'METHOD': 0, 'FLOW': 0, 'AREA':'TEMPORARY_OUTPUT'},
            {'ELEV': dem_src,      'X': x, 'Y': y, 'METHOD': 0, 'FLOW': 0, 'AREA':'TEMPORARY_OUTPUT'},
        ]))

        last_err = None
        for alg_id, param_sets in variants:
            if not self._has(alg_id):
                continue
            feedback.pushInfo(self.tr(f'Upslope Area (SAGA): {alg_id} …'))
            try:
                res = self._try_run(alg_id, param_sets, context, feedback)
                if res.get('AREA'):
                    return self._as_raster_src(res.get('AREA'))
            except Exception as e:
                last_err = e
                feedback.reportError(self.tr(f'{alg_id} lỗi/không có: {e}'))
        raise QgsProcessingException(self.tr('SAGA Upslope Area không chạy với các biến thể METHOD.'))

    # -------- Basin raster từ điểm: ưu tiên SAGA, fallback GRASS r.water.outlet --------
    def _basin_raster_from_point(self, dem_src, pt_xy, dem_crs_authid, context, feedback):
        # 1) SAGA Upslope Area (ưu tiên)
        try:
            area_src = self._saga_upslope_area_from_point(dem_src, pt_xy, context, feedback)
            area_layer = self._to_rlayer(area_src, 'area_ras')
            mask_res = processing.run('qgis:rastercalculator', {
                'EXPRESSION': f'("{area_layer.name()}@1" > 0)*1',
                'LAYERS': [area_layer],
                'CRS': dem_crs_authid,
                'EXTENT': area_layer.extent(),
                'OUTPUT': 'TEMPORARY_OUTPUT',
                'OUTPUT_DATATYPE': 5,
                'CELLSIZE': 0
            }, context=context, feedback=feedback)
            return mask_res['OUTPUT']
        except Exception as e:
            feedback.reportError(self.tr(f'SAGA Upslope Area thất bại, chuyển GRASS r.water.outlet: {e}'))

        # 2) GRASS r.watershed → drainage
        if not self._has('grass7:r.watershed') or not self._has('grass7:r.water.outlet'):
            raise QgsProcessingException(self.tr('Thiếu SAGA và/hoặc GRASS (r.watershed/r.water.outlet) để delineate lưu vực.'))

        feedback.pushInfo(self.tr('GRASS: r.watershed → drainage …'))
        try:
            gres = processing.run('grass7:r.watershed', {
                'elevation': dem_src,
                'threshold': 1,
                'accumulation': 'TEMPORARY_OUTPUT',
                'drainage': 'TEMPORARY_OUTPUT',
                'GRASS_REGION_PARAMETER': None,
                'GRASS_REGION_CELLSIZE_PARAMETER': 0,
                'GRASS_RASTER_FORMAT_META': '',
                'GRASS_RASTER_FORMAT_OPT': ''
            }, context=context, feedback=feedback)
            drainage_src = gres['drainage']
        except Exception as e:
            raise QgsProcessingException(self.tr(f'GRASS r.watershed lỗi: {e}'))

        # 3) GRASS r.water.outlet theo toạ độ
        feedback.pushInfo(self.tr('GRASS: r.water.outlet …'))
        try:
            outlet = processing.run('grass7:r.water.outlet', {
                'input': drainage_src,
                'coordinates': f'{pt_xy.x()},{pt_xy.y()}',
                'output': 'TEMPORARY_OUTPUT',
                'GRASS_REGION_PARAMETER': None,
                'GRASS_REGION_CELLSIZE_PARAMETER': 0,
                'GRASS_RASTER_FORMAT_META': '',
                'GRASS_RASTER_FORMAT_OPT': ''
            }, context=context, feedback=feedback)
            return outlet['output']
        except Exception as e:
            raise QgsProcessingException(self.tr(f'GRASS r.water.outlet lỗi: {e}'))

    # ============ MAIN ============
    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.P_DEM, context)
        if dem_layer is None:
            raise QgsProcessingException(self.tr('DEM không hợp lệ.'))

        # CRS DEM gốc dùng xuyên suốt
        dem_src = dem_layer
        dem_crs_authid = dem_layer.crs().authid()

        fill = self.parameterAsBool(parameters, self.P_FILL, context)
        snap_en = self.parameterAsBool(parameters, self.P_SNAP_ENABLE, context)
        auto_thresh = self.parameterAsBool(parameters, self.P_AUTO_THRESH, context)
        acc_thresh = self.parameterAsDouble(parameters, self.P_SNAP_ACC_THRESH, context)
        snap_radius = self.parameterAsDouble(parameters, self.P_SNAP_RADIUS, context)

        pour_src = self.parameterAsSource(parameters, self.P_POUR_LAYER, context)
        px = parameters.get(self.P_POUR_X, None); py = parameters.get(self.P_POUR_Y, None)
        px = None if px is None else float(px); py = None if py is None else float(py)
        pcrs = self.parameterAsCrs(parameters, self.P_POUR_CRS, context)

        # Lấy pour point & chuyển sang CRS DEM → tạo layer điểm tạm (CRS DEM)
        if pour_src:
            pt = self._first_point_from_layer(pour_src)
            if pt is None:
                raise QgsProcessingException(self.tr('Lớp điểm pour point không có đối tượng hợp lệ.'))
            try:
                src_crs = pour_src.sourceCrs()
            except Exception:
                src_crs = pcrs
            xform = QgsCoordinateTransform(src_crs, dem_layer.crs(), QgsProject.instance().transformContext())
            qpt = xform.transform(pt)
            pour_pt_dem = QgsPointXY(qpt.x(), qpt.y())
        else:
            if px is None or py is None:
                raise QgsProcessingException(self.tr('Hãy chọn lớp điểm hoặc nhập đủ X & Y.'))
            xform = QgsCoordinateTransform(pcrs, dem_layer.crs(), QgsProject.instance().transformContext())
            qpt = xform.transform(QgsPointXY(px, py))
            pour_pt_dem = QgsPointXY(qpt.x(), qpt.y())

        pt_layer_dem_crs = self._make_point_layer(pour_pt_dem, dem_crs_authid)

        # Fill sinks
        dem_used_src = self._as_raster_src(dem_src)
        if fill:
            dem_used_src = self._fill_sinks_resilient(dem_used_src, context, feedback)

        # --- Auto threshold (≈ 1 km²)
        if auto_thresh:
            try:
                px_m, py_m = self._estimate_pixel_size_m(dem_layer)
                pix_m = max(1e-6, (abs(px_m) + abs(py_m)) / 2.0)   # tránh chia 0
                acc_auto = max(1, int(round(1_000_000.0 / (pix_m * pix_m))))  # 1 km²
                feedback.pushInfo(self.tr(f"Auto threshold: kích thước ô ≈ {pix_m:.2f} m → ngưỡng ≈ {acc_auto} cells (~1 km²)."))
                acc_thresh = acc_auto
            except Exception as e:
                feedback.reportError(self.tr(f'Không tính được Auto threshold, dùng giá trị nhập tay: {acc_thresh}. Lỗi: {e}'))

        # Snap pour point (nếu bật)
        snapped_xy = pour_pt_dem
        if snap_en:
            acc_src = self._flow_accumulation_resilient(dem_used_src, context, feedback)
            snapped_xy = self._snap_point_to_stream(pt_layer_dem_crs, acc_src, acc_thresh, snap_radius, dem_crs_authid, context, feedback)

        # Tạo raster mask lưu vực từ điểm (SAGA ưu tiên; fallback GRASS r.water.outlet)
        mask_src = self._basin_raster_from_point(dem_used_src, snapped_xy, dem_crs_authid, context, feedback)

        # Polygonize & chọn DN==1
        poly_res = processing.run('gdal:polygonize', {
            'INPUT': mask_src, 'BAND':1, 'FIELD':'DN',
            'EIGHT_CONNECTEDNESS': False, 'EXTRA':'', 'OUTPUT':'TEMPORARY_OUTPUT'
        }, context=context, feedback=feedback)
        polys = poly_res['OUTPUT']

        sel_res = processing.run('native:extractbyattribute', {
            'INPUT': polys, 'FIELD':'DN', 'OPERATOR':0, 'VALUE':1, 'OUTPUT':'TEMPORARY_OUTPUT'
        }, context=context, feedback=feedback)
        current = sel_res['OUTPUT']

        # AOI (tuỳ chọn)
        aoi = self.parameterAsVectorLayer(parameters, self.P_AOI, context)
        if aoi:
            feedback.pushInfo(self.tr('Cắt lưu vực theo AOI…'))
            clip_res = processing.run('native:clip', {
                'INPUT': current, 'OVERLAY': aoi, 'OUTPUT':'TEMPORARY_OUTPUT'
            }, context=context, feedback=feedback)
            current = clip_res['OUTPUT']

        # Smooth/Simplify
        if self.parameterAsBool(parameters, self.P_SMOOTH_EN, context):
            iters = self.parameterAsInt(parameters, self.P_SMOOTH_IT, context)
            sm = processing.run('native:smoothgeometry', {
                'INPUT': current, 'ITERATIONS': iters, 'OFFSET':0.25, 'MAX_ANGLE':180, 'OUTPUT':'TEMPORARY_OUTPUT'
            }, context=context, feedback=feedback)
            current = sm['OUTPUT']

        if self.parameterAsBool(parameters, self.P_SIMP_EN, context):
            tol = self.parameterAsDouble(parameters, self.P_SIMP_TOL, context)
            if tol > 0:
                sp = processing.run('native:simplifygeometries', {
                    'INPUT': current, 'METHOD':0, 'TOLERANCE':tol, 'OUTPUT':'TEMPORARY_OUTPUT'
                }, context=context, feedback=feedback)
                current = sp['OUTPUT']

        out = self.parameterAsOutputLayer(parameters, self.P_OUTPUT, context)
        processing.run('native:savefeatures', {'INPUT': current, 'OUTPUT': out}, context=context, feedback=feedback)
        return {self.P_OUTPUT: out}
