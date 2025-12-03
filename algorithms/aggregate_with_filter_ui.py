# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
    QgsProcessingParameterString, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
    QgsProcessingException,
    QgsFields, QgsField, QgsFeature, QgsCoordinateReferenceSystem,
    QgsFeatureRequest, QgsExpression, QgsWkbTypes
)

def _tr(s):
    return QCoreApplication.translate("AggregateWithFilterUI", s)

# Số dòng tổng hợp hiển thị trong UI
MAX_SLOTS = 8

# Thứ tự hiển thị các hàm
AGG_FUNCS = [
    "SUM", "AVG", "MIN", "MAX",
    "STDDEV", "VARIANCE", "MEDIAN",
    "COUNT", "COUNT_DISTINCT",
]

class AggregateWithFilterUI(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    GROUP_FIELDS = "GROUP_FIELDS"
    FILTER_EXPR = "FILTER_EXPR"
    OUTPUT = "OUTPUT"
    def tr(self, text):
        return QCoreApplication.translate('AggregateWithFilterUI', text)
    def SLOT(self, i, name):  # tạo key tham số cho từng dòng
        return f"AGG{i}_{name}"

    def name(self): return "aggregate_with_filter_ui"
    def displayName(self): 
        return _tr("Thống kê có điều kiện (Dựng sẵn)")
    def group(self):
        return self.tr('Thống kê')

    def groupId(self):
        return 'thong_ke'

    def shortHelpString(self):
        return _tr(
            "Thống kê theo nhóm (GROUP BY) với điều kiện lọc và nhiều dòng tổng hợp.\n"
            "• Chọn trường nhóm (GROUP BY)\n"
            "• Với mỗi dòng: Bật, chọn HÀM, chọn TRƯỜNG, đặt ALIAS\n"
            "  - COUNT có thể để trống trường để thực hiện COUNT(*)\n"
            "• Điều kiện lọc là biểu thức QGIS (ví dụ: maldlr > 0 AND maldlr < 65)\n"
            "• Với các phép số (SUM/AVG/MIN/MAX/STDDEV/VARIANCE/MEDIAN), số thập phân của cột kết quả = precision của trường nguồn.\n"
            "Kết quả là bảng (NoGeometry). Tương thích QGIS 3.16 trở lên."
        )
    def createInstance(self): return AggregateWithFilterUI()

    def initAlgorithm(self, config=None):
        # Lớp đầu vào & Group-by & Filter
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, _tr("Lớp đầu vào"), [QgsProcessing.TypeVectorAnyGeometry]
        ))
        self.addParameter(QgsProcessingParameterField(
            self.GROUP_FIELDS, _tr("Trường nhóm (GROUP BY)"),
            parentLayerParameterName=self.INPUT, allowMultiple=True, optional=True
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FILTER_EXPR, _tr("Điều kiện lọc (Biểu thức QGIS, để trống nếu không lọc)"),
            defaultValue="", optional=True
        ))

        # Các slot tổng hợp
        for i in range(1, MAX_SLOTS + 1):
            self.addParameter(QgsProcessingParameterBoolean(
                self.SLOT(i, "ENABLE"), _tr(f"[{i}] Bật dòng tổng hợp"), defaultValue=(i == 1)
            ))
            self.addParameter(QgsProcessingParameterEnum(
                self.SLOT(i, "FUNC"), _tr(f"[{i}] Hàm tổng hợp"),
                options=AGG_FUNCS, defaultValue=0  # SUM
            ))
            self.addParameter(QgsProcessingParameterField(
                self.SLOT(i, "FIELD"), _tr(f"[{i}] Trường áp dụng (để trống nếu COUNT(*))"),
                parentLayerParameterName=self.INPUT, optional=True
            ))
            self.addParameter(QgsProcessingParameterString(
                self.SLOT(i, "ALIAS"), _tr(f"[{i}] Alias (tên cột kết quả)"),
                defaultValue="", optional=True
            ))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, _tr("Bảng kết quả"), type=QgsProcessing.TypeVector
        ))

    # Thu thập và kiểm tra danh sách tổng hợp từ UI
    def _collect_agg_specs(self, parameters, src_fields, context):
        specs = []
        for i in range(1, MAX_SLOTS + 1):
            if not self.parameterAsBoolean(parameters, self.SLOT(i, "ENABLE"), context):
                continue
            func_idx = self.parameterAsEnum(parameters, self.SLOT(i, "FUNC"), context)
            func = AGG_FUNCS[func_idx].lower()
            field_name = (self.parameterAsString(parameters, self.SLOT(i, "FIELD"), context) or "").strip()
            alias = (self.parameterAsString(parameters, self.SLOT(i, "ALIAS"), context) or "").strip()

            # Chuẩn hoá tên hàm
            if func == "countdistinct":
                func = "count_distinct"

            # COUNT(*) cho phép field rỗng
            if func == "count" and field_name == "":
                field_val = "*"
                vtype = None
                length = 0
                prec = 0
            else:
                if field_name == "":
                    raise QgsProcessingException(_tr(f"Dòng [{i}] thiếu trường."))
                idx = src_fields.indexFromName(field_name)
                if idx < 0:
                    raise QgsProcessingException(_tr(f"Dòng [{i}] – không tìm thấy trường: {field_name}"))
                fdef = src_fields[idx]
                vtype = fdef.type()
                length = getattr(fdef, "length", lambda: 0)() if hasattr(fdef, "length") else fdef.length()
                prec = getattr(fdef, "precision", lambda: 0)() if hasattr(fdef, "precision") else fdef.precision()
                field_val = field_name

                # Các hàm số yêu cầu trường số
                needs_numeric = func in ("sum", "avg", "min", "max", "stddev", "variance", "median")
                if needs_numeric and vtype not in (QVariant.Int, QVariant.Double):
                    raise QgsProcessingException(_tr(f"Dòng [{i}] – hàm {func.upper()} yêu cầu trường số: {field_name}"))

            specs.append({
                "func": func, "field": field_val, "alias": alias,
                "src_type": vtype, "src_len": length, "src_prec": prec
            })
        if not specs:
            raise QgsProcessingException(_tr("Chưa bật dòng tổng hợp nào."))
        return specs

    def processAlgorithm(self, parameters, context, feedback):
        src = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None:
            raise QgsProcessingException(_tr("Không đọc được lớp đầu vào"))
        src_fields = src.fields()

        # Group-by fields
        group_field_names = self.parameterAsFields(parameters, self.GROUP_FIELDS, context) or []
        group_idx = []
        for gn in group_field_names:
            idx = src_fields.indexFromName(gn)
            if idx < 0:
                raise QgsProcessingException(_tr("Không tìm thấy trường nhóm: {}").format(gn))
            group_idx.append(idx)

        # Agg specs từ UI
        agg_specs = self._collect_agg_specs(parameters, src_fields, context)

        # Filter
        filter_expr = (self.parameterAsString(parameters, self.FILTER_EXPR, context) or "").strip()
        request = QgsFeatureRequest(QgsExpression(filter_expr)) if filter_expr else QgsFeatureRequest()

        # Tích luỹ theo nhóm
        groups = {}

        # Khởi tạo accumulator theo hàm
        def _init_acc(spec):
            f = spec["func"]
            if f in ("sum", "avg"):
                return {"sum": 0.0, "count": 0}
            if f in ("min", "max"):
                return {"value": None}
            if f == "count":
                return {"count": 0}
            if f == "count_distinct":
                return {"set": set()}
            if f in ("stddev", "variance"):
                return {"n": 0, "mean": 0.0, "M2": 0.0}
            if f == "median":
                return {"values": []}  # cần danh sách để tính trung vị
            return {}

        def _update_welford(acc, x):
            # Thuật toán Welford cho mean & variance mẫu
            n = acc["n"] + 1
            delta = x - acc["mean"]
            mean = acc["mean"] + delta / n
            delta2 = x - mean
            M2 = acc["M2"] + delta * delta2
            acc["n"], acc["mean"], acc["M2"] = n, mean, M2

        def _update_acc(acc, spec, value):
            f = spec["func"]
            if f in ("sum", "avg"):
                if value is not None:
                    try:
                        x = float(value)
                        acc["sum"] += x
                        acc["count"] += 1
                    except Exception:
                        pass
            elif f == "min":
                if value is not None:
                    try:
                        x = float(value)
                        if acc["value"] is None or x < acc["value"]:
                            acc["value"] = x
                    except Exception:
                        pass
            elif f == "max":
                if value is not None:
                    try:
                        x = float(value)
                        if acc["value"] is None or x > acc["value"]:
                            acc["value"] = x
                    except Exception:
                        pass
            elif f == "count":
                if spec["field"] == "*":
                    acc["count"] += 1
                else:
                    if value is not None:
                        acc["count"] += 1
            elif f == "count_distinct":
                if value is not None:
                    acc["set"].add(value)
            elif f in ("stddev", "variance"):
                if value is not None:
                    try:
                        x = float(value)
                        _update_welford(acc, x)
                    except Exception:
                        pass
            elif f == "median":
                if value is not None:
                    try:
                        x = float(value)
                        acc["values"].append(x)
                    except Exception:
                        pass

        total = src.featureCount() or 1
        for i, feat in enumerate(src.getFeatures(request), start=1):
            if i % 1000 == 0:
                feedback.setProgress(int(100.0 * i / total))
            key = tuple(feat.attributes()[idx] for idx in group_idx) if group_idx else ()
            if key not in groups:
                groups[key] = {}
                for spec in agg_specs:
                    groups[key][(spec["func"], spec["field"])] = _init_acc(spec)

            for spec in agg_specs:
                if spec["func"] == "count" and spec["field"] == "*":
                    val = None
                else:
                    val = feat[spec["field"]]
                _update_acc(groups[key][(spec["func"], spec["field"])], spec, val)

        # ==== Xây schema output: cột nhóm + cột tổng hợp (precision khớp field nguồn) ====
        out_fields = QgsFields()
        # cột nhóm: giữ kiểu/len/prec gốc
        for idx in group_idx:
            out_fields.append(src_fields[idx])

        def _out_name(spec):
            if spec["alias"]:
                return spec["alias"]
            fn = spec["func"]
            fld = spec["field"]
            return f"{fn}_{'all' if fld == '*' else fld}"

        def _numeric_qvariant_for(spec):
            # tất cả hàm số trả về Double, nhưng precision lấy theo field nguồn
            return QVariant.Double

        agg_out_defs = []  # [(spec, out_index)]
        for spec in agg_specs:
            name = _out_name(spec)
            if spec["func"] in ("count", "count_distinct"):
                vtype = QVariant.Int
                out_fields.append(QgsField(name, vtype))
            else:
                vtype = _numeric_qvariant_for(spec)
                # lấy len/prec từ field nguồn (nếu có)
                src_len = spec.get("src_len", 0) or 20
                src_prec = spec.get("src_prec", 0) or 0
                # đảm bảo tổng chiều dài hợp lý (đặc biệt với SUM/STDDEV…)
                total_len = max(src_len, src_prec + 6)  # chừa chỗ cho phần nguyên
                out_fields.append(QgsField(name, vtype, len=int(total_len), prec=int(src_prec)))
            agg_out_defs.append((spec, out_fields.indexFromName(name)))

        # ==== Tạo sink NoGeometry ====
        sink, sink_id = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            out_fields, QgsWkbTypes.NoGeometry, QgsCoordinateReferenceSystem()
        )
        if sink is None:
            raise QgsProcessingException(_tr("Không tạo được bảng đầu ra"))

        # ==== Tính toán giá trị cuối cùng và ghi kết quả ====
        def _final_value(spec, acc):
            f = spec["func"]
            if f == "sum":
                return acc["sum"]
            if f == "avg":
                return (acc["sum"] / acc["count"]) if acc["count"] > 0 else None
            if f in ("min", "max"):
                return acc["value"]
            if f == "count":
                return acc["count"]
            if f == "count_distinct":
                return len(acc["set"])
            if f in ("stddev", "variance"):
                n = acc["n"]
                if n <= 1:
                    return None
                var_sample = acc["M2"] / (n - 1)  # phương sai mẫu
                return (var_sample ** 0.5) if f == "stddev" else var_sample
            if f == "median":
                vals = acc["values"]
                if not vals:
                    return None
                vals.sort()
                m = len(vals)
                mid = m // 2
                if m % 2 == 1:
                    return vals[mid]
                else:
                    return (vals[mid - 1] + vals[mid]) / 2.0
            return None

        for key, accs in groups.items():
            out_feat = QgsFeature(out_fields)
            attrs = []
            # nhóm
            if key:
                attrs.extend(list(key))
            # tổng hợp
            for spec, col_idx in agg_out_defs:
                val = _final_value(spec, accs[(spec["func"], spec["field"])])
                attrs.append(val)
            out_feat.setAttributes(attrs)
            sink.addFeature(out_feat)

        return {self.OUTPUT: sink_id}
