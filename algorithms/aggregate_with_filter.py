# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
    QgsProcessingParameterString, QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsFields, QgsField, QgsFeature, QgsCoordinateReferenceSystem,
    QgsFeatureRequest, QgsExpression, QgsWkbTypes
)
import re

def _tr(s):
    return QCoreApplication.translate("AggregateWithFilter", s)

class AggregateWithFilter(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    GROUP_FIELDS = "GROUP_FIELDS"
    AGG_LIST = "AGG_LIST"
    FILTER_EXPR = "FILTER_EXPR"
    OUTPUT = "OUTPUT"
    def tr(self, text):
        return QCoreApplication.translate('AggregateWithFilterUI', text)
    def name(self):
        return "aggregate_with_filter"

    def displayName(self):
        return _tr("Thống kê có điều kiện")
    def group(self):
        return self.tr('Thống kê')

    def groupId(self):
        return 'thong_ke'
    def shortHelpString(self):
        return _tr(
            "Thực hiện thống kê theo nhóm giống GROUP BY, với điều kiện lọc.\n\n"
            "• Chọn các trường nhóm (ví dụ: tinh, xa, ldlr)\n"
            "• Nhập danh sách tổng hợp ở ô 'Các phép tổng hợp':\n"
            "   - Cú pháp: func(field) [AS alias], phân tách bằng dấu phẩy/ chấm phẩy\n"
            "   - Hỗ trợ: SUM, AVG, MIN, MAX, COUNT, COUNT_DISTINCT\n"
            "   - Ví dụ:  sum(dtich) as dtich_sum, sum(mgo) as mgo_sum, count(*) as n\n"
            "• Điều kiện lọc dùng biểu thức QGIS (ví dụ: maldlr > 0 AND maldlr < 65)\n\n"
            "Kết quả là bảng (no geometry). Các cột kết quả số giữ đúng precision theo trường nguồn."
        )

    def createInstance(self):
        return AggregateWithFilter()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, _tr("Lớp đầu vào"),
            [QgsProcessing.TypeVectorAnyGeometry]
        ))
        self.addParameter(QgsProcessingParameterField(
            self.GROUP_FIELDS, _tr("Trường nhóm (GROUP BY)"),
            parentLayerParameterName=self.INPUT, allowMultiple=True, optional=True
        ))
        self.addParameter(QgsProcessingParameterString(
            self.AGG_LIST,
            _tr("Các phép tổng hợp (ví dụ: sum(dtich) as dtich_sum, sum(mgo) as mgo_sum)"),
            defaultValue="sum(dtich) as dtich_sum"
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FILTER_EXPR,
            _tr("Điều kiện lọc (Biểu thức QGIS, để trống nếu không lọc)"),
            defaultValue="", optional=True
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, _tr("Bảng kết quả"),
            type=QgsProcessing.TypeVector
        ))

    # ---------- parse "sum(dtich) as dtich_sum" ----------
    _item_re = re.compile(
        r"^\s*(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*"
        r"\(\s*(?P<field>[^\)]*)\s*\)\s*(?:AS\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?\s*$",
        flags=re.IGNORECASE
    )

    def _parse_agg_list(self, text):
        """
        Trả về danh sách dict: {func, field, alias}
        field có thể là '*' cho count(*)
        """
        if not text:
            raise QgsProcessingException(_tr("Chưa khai báo phép tổng hợp."))
        parts = [p.strip() for p in re.split(r"[;,]", text) if p.strip()]
        aggs = []
        for p in parts:
            m = self._item_re.match(p)
            if not m:
                raise QgsProcessingException(_tr("Không hiểu mục tổng hợp: '{}'").format(p))
            func = m.group("func").lower()
            field = m.group("field").strip()
            alias = m.group("alias") or ""
            if field == "":
                raise QgsProcessingException(_tr("Thiếu tên trường trong '{}'").format(p))
            if func not in ("sum", "avg", "min", "max", "count", "count_distinct", "countdistinct"):
                raise QgsProcessingException(_tr("Hàm không hỗ trợ trong '{}': {}").format(p, func))
            if func == "countdistinct":
                func = "count_distinct"
            if func == "count" and field == "*":
                pass  # count(*) hợp lệ
            aggs.append({"func": func, "field": field, "alias": alias})
        return aggs

    def processAlgorithm(self, parameters, context, feedback):
        src = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None:
            raise QgsProcessingException(_tr("Không đọc được lớp đầu vào"))

        # Lấy danh sách trường nhóm
        group_field_names = self.parameterAsFields(parameters, self.GROUP_FIELDS, context)
        group_field_names = group_field_names or []

        # Parse danh sách tổng hợp
        agg_list_str = self.parameterAsString(parameters, self.AGG_LIST, context)
        agg_specs = self._parse_agg_list(agg_list_str)

        # Kiểm tra fields tồn tại, gom meta length/precision
        src_fields = src.fields()

        def _field_index(name):
            idx = src_fields.indexFromName(name)
            if idx < 0:
                raise QgsProcessingException(_tr("Không tìm thấy trường: {}").format(name))
            return idx

        def _is_numeric_qvariant(qvt):
            return qvt in (QVariant.Int, QVariant.Double)

        # group-by indices
        group_idx = []
        for gn in group_field_names:
            group_idx.append(_field_index(gn))

        # enrich agg_specs with source meta
        for spec in agg_specs:
            func = spec["func"]
            fld = spec["field"]
            if not (func == "count" and fld == "*"):
                idx = _field_index(fld)
                fdef = src_fields[idx]
                spec["_src_type"] = fdef.type()
                # lấy length/precision nếu có (tuỳ phiên bản)
                try:
                    spec["_src_len"] = fdef.length()
                except Exception:
                    spec["_src_len"] = 0
                try:
                    spec["_src_prec"] = fdef.precision()
                except Exception:
                    spec["_src_prec"] = 0

                # Ràng buộc kiểu số cho SUM/AVG
                if func in ("sum", "avg") and not _is_numeric_qvariant(spec["_src_type"]):
                    raise QgsProcessingException(_tr("Hàm {} yêu cầu trường số: {}").format(func.upper(), fld))
                # MIN/MAX: cho phép mọi kiểu; nếu số sẽ xuất double với precision nguồn,
                # nếu không số sẽ giữ kiểu nguyên gốc.

        # Biểu thức lọc
        filter_expr = (self.parameterAsString(parameters, self.FILTER_EXPR, context) or "").strip()
        request = QgsFeatureRequest(QgsExpression(filter_expr)) if filter_expr else QgsFeatureRequest()

        # Chuẩn bị cấu trúc tích luỹ
        groups = {}

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
            return {}

        def _update_acc(acc, spec, value):
            f = spec["func"]
            if f == "sum":
                if value is not None:
                    try:
                        acc["sum"] += float(value)
                        acc["count"] += 1
                    except Exception:
                        pass
            elif f == "avg":
                if value is not None:
                    try:
                        acc["sum"] += float(value)
                        acc["count"] += 1
                    except Exception:
                        pass
            elif f == "min":
                # giữ chính value (để MIN của String, Date... hoạt động)
                if value is not None and (acc["value"] is None or value < acc["value"]):
                    acc["value"] = value
            elif f == "max":
                if value is not None and (acc["value"] is None or value > acc["value"]):
                    acc["value"] = value
            elif f == "count":
                if spec["field"] == "*":
                    acc["count"] += 1
                else:
                    if value is not None:
                        acc["count"] += 1
            elif f == "count_distinct":
                if value is not None:
                    acc["set"].add(value)

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

        # ====== Xây schema output: precision theo field nguồn ======
        out_fields = QgsFields()

        # Thêm cột nhóm (giữ kiểu/len/prec gốc)
        for idx, name in zip(group_idx, group_field_names):
            out_fields.append(src_fields[idx])

        def _out_name(spec):
            if spec["alias"]:
                return spec["alias"]
            func = spec["func"].lower()
            fld = spec["field"]
            if func == "count" and fld == "*":
                return "count"
            return f"{func}_{fld}"

        def _append_numeric_out_field(name, src_len, src_prec):
            # Double + precision như nguồn, length nới cho đủ phần nguyên
            total_len = max(int(src_len or 0), int(src_prec or 0) + 6)
            out_fields.append(QgsField(name, QVariant.Double, len=total_len, prec=int(src_prec or 0)))

        agg_out_defs = []  # [(spec, out_index)]
        for spec in agg_specs:
            name = _out_name(spec)
            f = spec["func"]
            if f in ("sum", "avg"):
                src_len = spec.get("_src_len", 20)
                src_prec = spec.get("_src_prec", 0)
                _append_numeric_out_field(name, src_len, src_prec)
            elif f in ("min", "max"):
                src_type = spec.get("_src_type", QVariant.Double)
                if src_type in (QVariant.Int, QVariant.Double):
                    src_len = spec.get("_src_len", 20)
                    src_prec = spec.get("_src_prec", 0)
                    _append_numeric_out_field(name, src_len, src_prec)
                else:
                    # giữ kiểu gốc (String, Date, ...)
                    idx_src = src_fields.indexFromName(spec["field"])
                    fdef = src_fields[idx_src]
                    # Sao chép type; length/precision nếu áp dụng
                    try:
                        out_fields.append(QgsField(name, fdef.type(), len=fdef.length(), prec=fdef.precision()))
                    except Exception:
                        out_fields.append(QgsField(name, fdef.type()))
            elif f in ("count", "count_distinct"):
                out_fields.append(QgsField(name, QVariant.Int))
            else:
                # fallback: Double
                src_len = spec.get("_src_len", 20)
                src_prec = spec.get("_src_prec", 0)
                _append_numeric_out_field(name, src_len, src_prec)

            agg_out_defs.append((spec, out_fields.indexFromName(name)))

        # Tạo sink (bảng, NoGeometry)
        sink, sink_id = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            out_fields, QgsWkbTypes.NoGeometry,
            QgsCoordinateReferenceSystem()
        )
        if sink is None:
            raise QgsProcessingException(_tr("Không tạo được bảng đầu ra"))

        # Tính giá trị cuối cùng theo accumulator
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
            return None

        # Ghi kết quả
        for key, accs in groups.items():
            out_feat = QgsFeature(out_fields)
            attrs = []
            # nhóm
            if key:
                attrs.extend(list(key))
            # tổng hợp
            for spec, out_idx in agg_out_defs:
                val = _final_value(spec, accs[(spec["func"], spec["field"])])
                attrs.append(val)

            out_feat.setAttributes(attrs)
            sink.addFeature(out_feat)

        return {self.OUTPUT: sink_id}
