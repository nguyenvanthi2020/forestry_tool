# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QVariant, QCoreApplication
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
    QgsProcessingException,
    QgsVectorLayer, QgsFields, QgsField, QgsFeature,
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
    QgsCoordinateReferenceSystem, QgsWkbTypes, QgsPointXY
)
from itertools import islice

def _tr(s):
    return QCoreApplication.translate("ReorderFieldsAlgorithm", s)

# ===== SCHEMA: CHỈ fix cứng size cho trường SỐ; TEXT auto-fit theo dữ liệu =====
# (name_lower, QVariantType, length_hint, precision_hint)
SCHEMA = [
    ("tt" , QVariant.Int , 9, 0),
    ("id" , QVariant.Int , 9, 0),
    ("matinh" , QVariant.Int , 3, 0),
    ("mahuyen" , QVariant.Int , 4, 0),
    ("maxa" , QVariant.Int , 5, 0),
    ("xa" , QVariant.String , 30, 0),
    ("tk" , QVariant.String , 5, 0),
    ("khoanh" , QVariant.String , 5, 0),
    ("lo" , QVariant.String , 10, 0),
    ("thuad" , QVariant.Int , 4, 0),
    ("tobando" , QVariant.String , 5, 0),
    ("diadanh" , QVariant.String , 50, 0),
    ("dtich" , QVariant.Double , 9, 2),
    ("nggocr" , QVariant.Int , 2, 0),
    ("ldlr" , QVariant.String , 5, 0),
    ("maldlr" , QVariant.Int , 3, 0),
    ("sldlr" , QVariant.String , 30, 0),
    ("namtr" , QVariant.Int , 4, 0),
    ("captuoi" , QVariant.Int , 2, 0),
    ("ktan" , QVariant.Int , 2, 0),
    ("nggocrt" , QVariant.Int , 2, 0),
    ("thanhrung" , QVariant.Int , 2, 0),
    ("mgo" , QVariant.Double , 7, 2),
    ("mtn" , QVariant.Double , 9, 3),
    ("mgolo" , QVariant.Double , 10, 2),
    ("mtnlo" , QVariant.Double , 10, 3),
    ("lapdia" , QVariant.Int , 2, 0),
    ("malr3" , QVariant.Int , 2, 0),
    ("mdsd" , QVariant.String , 5, 0),
    ("mamdsd" , QVariant.Int , 2, 0),
    ("dtuong" , QVariant.Int , 2, 0),
    ("churung" , QVariant.String , 50, 0),
    ("machur" , QVariant.Int , 5, 0),
    ("trchap" , QVariant.Int , 2, 0),
    ("quyensd" , QVariant.Int , 2, 0),
    ("thoihansd" , QVariant.Int , 2, 0),
    ("khoan" , QVariant.Int , 2, 0),
    ("nqh" , QVariant.Int , 2, 0),
    ("nguoink" , QVariant.String , 30, 0),
    ("nguoitrch" , QVariant.String , 30, 0),
    ("mangnk" , QVariant.Int , 5, 0),
    ("mangtrch" , QVariant.Int , 5, 0),
    ("ngsinh" , QVariant.Int , 2, 0),
    ("kd" , QVariant.Double , 11, 2),
    ("vd" , QVariant.Double , 10, 2),
    ("capkd" , QVariant.Double , 5, 1),
    ("capvd" , QVariant.Double , 5, 2),
    ("locu" , QVariant.String , 10, 0),
    ("vitrithua" , QVariant.Int , 2, 0),
    ("tinh" , QVariant.String , 30, 0),
    ("huyen" , QVariant.String , 30, 0),
]

# alias tên trường nguồn → tên đích (không phân biệt hoa/thường)
SOURCE_ALIAS = {
    # "id": "OBJECTID",
    # "maxa": "MaXa",
}

# biểu thức ép kiểu riêng (nếu cần), trong dấu " field name " là case-insensitive theo tên nguồn
CAST_MAP = {
    # "dtich": 'round(to_real("DTICH_GOC"), 2)',
}

DBF_MAX_CHAR = 254  # giới hạn text của Shapefile/DBF

class ReorderFieldsAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    ADD_MISSING_SCHEMA = "ADD_MISSING_SCHEMA"
    EXTRA_FIELDS_POLICY = "EXTRA_FIELDS_POLICY"

    EXTRA_APPEND = 0  # giữ trường ngoài schema và đưa về cuối
    EXTRA_DROP = 1    # xoá trường ngoài schema

    def tr(self, text): return QCoreApplication.translate('ReorderFields', text)
    def name(self): return "reorder_cast_mixedsizes_sink"
    def displayName(self): return _tr("Sắp xếp và chuẩn hóa trường (51 trường)")
    def group(self): return self.tr('Tiện ích trường')
    def groupId(self): return 'field_utils'
    def shortHelpString(self):
        return _tr(
            "• Trường SỐ (Int/Double) trong SCHEMA: fix cứng length/precision như SCHEMA.\n"
            "• Trường TEXT trong SCHEMA: auto-fit theo độ dài lớn nhất của dữ liệu (giới hạn DBF 254 cho .shp).\n"
            "• Trường ngoài SCHEMA: Append (giữ, text auto-fit; số giữ size gốc) hoặc Drop (xoá).\n"
            "• OUTPUT là FeatureSink → có menu 'Change File Encoding…' cho Shapefile."
        )
    def createInstance(self): return ReorderFieldsAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, _tr("Lớp đầu vào"), [QgsProcessing.TypeVectorAnyGeometry]
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, _tr("Lớp đầu ra (.gpkg/.shp hoặc tạm)"),
            type=QgsProcessing.TypeVectorAnyGeometry
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.ADD_MISSING_SCHEMA, _tr("Bổ sung trường SCHEMA nếu bị thiếu"), defaultValue=True
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.EXTRA_FIELDS_POLICY,
            _tr("Xử lý các trường KHÔNG có trong SCHEMA"),
            options=[_tr("Đưa về cuối (Append)"), _tr("Xoá (Drop)")],
            defaultValue=self.EXTRA_APPEND
        ))

    # ---------- tiện ích field/expr ----------
    @staticmethod
    def _make_field(name_lower, vtype, length, prec):
        # clamp an toàn + giới hạn DBF
        if vtype == QVariant.String:
            length = max(1, min(int(length or 1), DBF_MAX_CHAR))
            return QgsField(name_lower, vtype, len=length)
        if vtype == QVariant.Int:
            length = max(int(length or 1), 1)
            return QgsField(name_lower, vtype, len=length)
        if vtype == QVariant.Double:
            length = int(length or 1)
            prec = int(prec or 0)
            if length <= prec + 1:
                length = prec + 2
            return QgsField(name_lower, vtype, len=length, prec=prec)
        return QgsField(name_lower, vtype, len=int(length or 0), prec=int(prec or 0))

    @staticmethod
    def _default_cast_expr(vtype, src_name_exact):
        if src_name_exact is None: return "NULL"
        q = f'"{src_name_exact}"'
        if vtype == QVariant.Int:    return f"to_int({q})"
        if vtype == QVariant.Double: return f"to_real({q})"
        if vtype == QVariant.String: return f"to_string({q})"
        if vtype == QVariant.Date:   return f"to_date({q})"
        if vtype == QVariant.DateTime: return f"to_datetime({q})"
        if vtype == QVariant.Time:     return f"to_time({q})"
        return q

    @staticmethod
    def _normalize_expr_field_quotes(expr_str, src_lc2exact):
        out, i, s, n = [], 0, expr_str, len(expr_str)
        while i < n:
            ch = s[i]
            if ch == '"':
                j = i + 1; buf = []
                while j < n and s[j] != '"': buf.append(s[j]); j += 1
                name_lc = "".join(buf).lower()
                exact = src_lc2exact.get(name_lc)
                out.append(f'"{exact if exact else "".join(buf)}"')
                i = j + 1
            else:
                out.append(ch); i += 1
        return "".join(out)

    # ---------- ép hình học 2D + chuẩn hoá single/multi ----------
    @staticmethod
    def _geom_to_2d_target(geom, target_wkb):
        if geom is None or geom.isEmpty():
            return geom
        gtype = QgsWkbTypes.geometryType(target_wkb)
        want_multi = QgsWkbTypes.isMultiType(target_wkb)
        try:
            if gtype == QgsWkbTypes.PolygonGeometry:
                if geom.isMultipart():
                    mpoly = geom.asMultiPolygon()
                    if not mpoly: return geom
                    from qgis.core import QgsGeometry
                    return QgsGeometry.fromMultiPolygonXY(mpoly) if want_multi else QgsGeometry.fromPolygonXY(mpoly[0])
                else:
                    poly = geom.asPolygon()
                    if not poly: return geom
                    from qgis.core import QgsGeometry
                    return QgsGeometry.fromMultiPolygonXY([poly]) if want_multi else QgsGeometry.fromPolygonXY(poly)
            elif gtype == QgsWkbTypes.LineGeometry:
                if geom.isMultipart():
                    mline = geom.asMultiPolyline()
                    if not mline: return geom
                    from qgis.core import QgsGeometry
                    return QgsGeometry.fromMultiPolylineXY(mline) if want_multi else QgsGeometry.fromPolylineXY(mline[0])
                else:
                    line = geom.asPolyline()
                    if not line: return geom
                    from qgis.core import QgsGeometry
                    return QgsGeometry.fromMultiPolylineXY([line]) if want_multi else QgsGeometry.fromPolylineXY(line)
            elif gtype == QgsWkbTypes.PointGeometry:
                if geom.isMultipart():
                    mpts = geom.asMultiPoint()
                    from qgis.core import QgsGeometry
                    return QgsGeometry.fromMultiPointXY(mpts) if want_multi else QgsGeometry.fromPointXY(QgsPointXY(mpts[0])) if mpts else geom
                else:
                    pt = geom.asPoint()
                    from qgis.core import QgsGeometry
                    return QgsGeometry.fromMultiPointXY([QgsPointXY(pt)]) if want_multi else QgsGeometry.fromPointXY(QgsPointXY(pt))
        except Exception:
            return geom
        return geom

    def processAlgorithm(self, parameters, context, feedback):
        # Input
        src = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None:
            raise QgsProcessingException("Không đọc được lớp đầu vào")
        in_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if not isinstance(in_layer, QgsVectorLayer) or not in_layer.isValid():
            raise QgsProcessingException("Lớp đầu vào không hợp lệ")

        add_missing = self.parameterAsBoolean(parameters, self.ADD_MISSING_SCHEMA, context)
        extra_policy = self.parameterAsEnum(parameters, self.EXTRA_FIELDS_POLICY, context)

        # map tên field nguồn: lower -> exact
        src_fields = in_layer.fields()
        lc2exact = {f.name().lower(): f.name() for f in src_fields}
        # --- BỎ cột có tên " id" (hoặc tên strip() == "id" nhưng không đúng "id") ---
        bad_id_fields = {f.name() for f in src_fields
                         if f.name().strip().lower() == "id" and f.name() != "id"}

        if bad_id_fields:
            # loại khỏi map tên nguồn
            lc2exact = {k: v for k, v in lc2exact.items() if v not in bad_id_fields}

        schema_names = [name for (name, _t, _l, _p) in SCHEMA]
        schema_name_set = set(schema_names)

        # Build danh sách đích (SCHEMA trước, rồi EXTRA nếu Append)
        def resolve_src(target_lower):
            alias = SOURCE_ALIAS.get(target_lower)
            if alias:
                return lc2exact.get(alias.lower())
            return lc2exact.get(target_lower)

        targets = []   # [(name_lower, vtype, lhint, phint)]
        exprs_str = {} # target_idx -> expression string
        covered_src_exact = set()

        # 1) SCHEMA: luôn theo thứ tự định nghĩa
        for (name_lower, vtype, lhint, phint) in SCHEMA:
            src_exact = resolve_src(name_lower)
            if src_exact is None and not add_missing:
                continue
            idx = len(targets)
            targets.append((name_lower, vtype, lhint, phint))
            if name_lower in CAST_MAP:
                exprs_str[idx] = self._normalize_expr_field_quotes(CAST_MAP[name_lower], lc2exact)
            else:
                exprs_str[idx] = self._default_cast_expr(vtype, src_exact)
            if src_exact:
                covered_src_exact.add(src_exact)

        # 2) EXTRA (Append)
        if extra_policy == self.EXTRA_APPEND:
            for fdef in src_fields:
                src_exact = fdef.name()
                # bỏ luôn cột " id" (và các biến thể tên có khoảng trắng quanh) khỏi EXTRA
                if src_exact in bad_id_fields:
                    continue

                if src_exact in covered_src_exact:
                    continue
                if fdef.name().lower() in schema_name_set:
                    continue
                name_lower = src_exact.lower()
                vtype = fdef.type()
                # size gốc – sẽ dùng cho số; text vẫn sẽ auto-fit bên dưới
                try:
                    lhint = fdef.length()
                except Exception:
                    lhint = 0
                try:
                    phint = fdef.precision()
                except Exception:
                    phint = 0
                idx = len(targets)
                targets.append((name_lower, vtype, lhint, phint))
                if name_lower in CAST_MAP:
                    exprs_str[idx] = self._normalize_expr_field_quotes(CAST_MAP[name_lower], lc2exact)
                else:
                    exprs_str[idx] = self._default_cast_expr(vtype, src_exact)
                covered_src_exact.add(src_exact)

        # === PASS: đo riêng độ dài TEXT để auto-fit ===
        # chỉ cần cho TEXT (trong SCHEMA và EXTRA)
        str_maxlen = {}
        exprs = {i: QgsExpression(s) for i, s in exprs_str.items()}
        ctx = QgsExpressionContext()
        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(in_layer))

        total = src.featureCount() or 1
        for k, feat in enumerate(in_layer.getFeatures(), start=1):
            if k % 1000 == 0:
                feedback.setProgress(int(100.0 * k / total))
            ctx.setFeature(feat)
            for i, (name_lower, vtype, lhint, phint) in enumerate(targets):
                if vtype != QVariant.String:
                    continue
                ex = exprs.get(i)
                if not ex:
                    continue
                try:
                    val = ex.evaluate(ctx)
                    if ex.hasEvalError() or val is None:
                        continue
                    ln = len(str(val))
                    if i not in str_maxlen or ln > str_maxlen[i]:
                        str_maxlen[i] = ln
                except Exception:
                    pass

        # === Lập schema output: SỐ theo SCHEMA; TEXT theo maxlen ===
        tgt_fields = QgsFields()
        for i, (name_lower, vtype, lhint, phint) in enumerate(targets):
            if name_lower in schema_name_set:
                # Trường thuộc SCHEMA
                if vtype == QVariant.String:
                    # text: auto-fit theo dữ liệu (ưu tiên maxlen), fallback dùng lhint
                    length = max(1, str_maxlen.get(i, int(lhint or 1)))
                    tgt_fields.append(self._make_field(name_lower, vtype, length, 0))
                elif vtype in (QVariant.Int, QVariant.Double):
                    # số: ép đúng size của SCHEMA
                    tgt_fields.append(self._make_field(name_lower, vtype, int(lhint or 0), int(phint or 0)))
                else:
                    # kiểu khác (hiếm): theo SCHEMA
                    tgt_fields.append(self._make_field(name_lower, vtype, int(lhint or 0), int(phint or 0)))
            else:
                # EXTRA fields
                if vtype == QVariant.String:
                    # text extra: auto-fit theo dữ liệu; nếu không có dữ liệu thì giữ size gốc lhint
                    length = max(1, str_maxlen.get(i, int(lhint or 1)))
                    tgt_fields.append(self._make_field(name_lower, vtype, length, 0))
                elif vtype in (QVariant.Int, QVariant.Double):
                    # số extra: giữ size gốc theo lớp đầu vào
                    tgt_fields.append(self._make_field(name_lower, vtype, int(lhint or 0), int(phint or 0)))
                else:
                    tgt_fields.append(self._make_field(name_lower, vtype, int(lhint or 0), int(phint or 0)))

        # Kiểu hình học đích: MULTI + 2D
        target_wkb = QgsWkbTypes.multiType(in_layer.wkbType())
        target_wkb = QgsWkbTypes.dropZ(QgsWkbTypes.dropM(target_wkb))

        # Sink
        sink, sink_id = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            tgt_fields, target_wkb,
            in_layer.sourceCrs() if in_layer.sourceCrs().isValid() else QgsCoordinateReferenceSystem()
        )
        if sink is None:
            raise QgsProcessingException("Không tạo được lớp đầu ra (sink).")

        # Ghi dữ liệu
        total = src.featureCount() or 1
        failed = 0
        failed_fids = []
        for k, feat in enumerate(in_layer.getFeatures(), start=1):
            if k % 1000 == 0:
                feedback.setProgress(int(100.0 * k / total))

            ctx.setFeature(feat)
            out_f = QgsFeature(tgt_fields)

            # Hình học (chuẩn hoá 2D/multi)
            g = feat.geometry()
            out_f.setGeometry(self._geom_to_2d_target(g, target_wkb))

            # Thuộc tính
            attrs = [None] * len(targets)
            for i in range(len(targets)):
                ex = exprs.get(i)
                try:
                    v = ex.evaluate(ctx) if ex else None
                    if ex and ex.hasEvalError():
                        v = None
                except Exception:
                    v = None
                attrs[i] = v
            out_f.setAttributes(attrs)

            ok = sink.addFeature(out_f)
            if not ok:
                ok = sink.addFeature(out_f)
            if not ok:
                failed += 1
                failed_fids.append(feat.id())

        if failed:
            sample = list(islice(failed_fids, 50))
            try:
                self.messageLog().logMessage(
                    f"[ReorderFields] Không ghi được {failed} đối tượng. FID ví dụ: {sample}{' ...' if len(failed_fids)>50 else ''}",
                    "Processing", 1
                )
            except Exception:
                pass

        return {self.OUTPUT: sink_id}
