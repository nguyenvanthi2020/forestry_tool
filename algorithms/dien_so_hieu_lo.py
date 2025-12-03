# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingContext,
    QgsFeatureRequest,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsFeatureSink,
    QgsVectorLayer,
    QgsVectorDataProvider,
)

import math


class DienSoHieuLoAlg(QgsProcessingAlgorithm):
    P_INPUT = "INPUT"
    P_OUTPUT = "OUTPUT"
    P_SELECTED_ONLY = "SELECTED_ONLY"
    P_CREATE_IF_MISSING = "CREATE_IF_MISSING"
    P_FIELD_MAXA = "FIELD_MAXA"
    P_FIELD_TK = "FIELD_TK"
    P_FIELD_KHOANH = "FIELD_KHOANH"
    P_FIELD_KD = "FIELD_KD"
    P_FIELD_VD = "FIELD_VD"
    P_FIELD_LO = "FIELD_LO"

    P_METHOD = "METHOD"
    P_IN_PLACE = "IN_PLACE"

    METHOD_DINH_LO = 0
    METHOD_TAM_LO = 1
    METHOD_TU_DO = 2

    def tr(self, text):
        return QCoreApplication.translate("DienSoHieuLoAlg", text)

    def name(self):
        return "dien_so_hieu_lo_text_by_vd_desc_kd_asc"

    def displayName(self):
        return self.tr("Điền số hiệu lô")

    def group(self):
        return self.tr("Tiện ích trường")

    def groupId(self):
        return "field_utils"

    def shortHelpString(self):
        return self.tr(
            "Điền số hiệu lô là một công cụ miễn phí hỗ trợ xây dựng bản đồ lâm nghiệp theo hướng dẫn tại "
            "Quyết định số 145/QĐ-CKL-CĐS. Trong lớp bản đồ cần có các trường sau: mã xã (maxa, số), "
            "tiểu khu (tk, chữ), khoảnh (khoanh, chữ), lô (lo, chữ), kinh độ (kd, số) và vĩ độ (vd, số). "
            "Bạn có thể chọn phương thức điền số hiệu lô:\n"
            "  • Điền theo quy tắc đỉnh lô: từ trên xuống dưới theo đỉnh lô phía Bắc và từ trái sang phải theo đỉnh lô phía Tây.\n"
            "  • Điền theo quy tắc tâm lô:  từ trên xuống dưới và từ trái sang phải theo vị trí tâm lô.\n"
            "  • Điền tự do: không theo quy tắc từ trên xuống dưới và từ trái sang phải.\n"
            "Tuỳ chọn 'Cập nhật vào lớp (in-place)' để chỉnh sửa trực tiếp lớp đầu vào (không sinh lớp mới).\n "
            "Chú ý: kd, vd được tính theo hệ toạ độ của lớp đầu vào."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.P_INPUT, self.tr("Lớp đầu vào"), [QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorAnyGeometry]
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.P_METHOD,
                self.tr("Phương thức điền số hiệu lô"),
                options=[
                    self.tr("Điền theo quy tắc đỉnh lô"),
                    self.tr("Điền theo quy tắc tâm lô"),
                    self.tr("Điền tự do"),
                ],
                defaultValue=self.METHOD_DINH_LO,
            )
        )


        # --- Các trường ---
        self.addParameter(
            QgsProcessingParameterField(
                self.P_FIELD_MAXA,
                self.tr("Trường mã xã (Số)"),
                parentLayerParameterName=self.P_INPUT,
                type=QgsProcessingParameterField.Numeric,
                defaultValue="maxa"
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.P_FIELD_TK,
                self.tr("Trường Tiểu khu (Chữ)"),
                parentLayerParameterName=self.P_INPUT,
                type=QgsProcessingParameterField.String,
                defaultValue="tk"
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.P_FIELD_KHOANH,
                self.tr("Trường Khoảnh (Chữ)"),
                parentLayerParameterName=self.P_INPUT,
                type=QgsProcessingParameterField.String,
                defaultValue="khoanh"
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.P_FIELD_KD,
                self.tr("Trường Kinh độ (Số)"),
                parentLayerParameterName=self.P_INPUT,
                type=QgsProcessingParameterField.Numeric,
                defaultValue="kd"
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.P_FIELD_VD,
                self.tr("Trường Vĩ độ (Số)"),
                parentLayerParameterName=self.P_INPUT,
                type=QgsProcessingParameterField.Numeric,
                defaultValue="vd"
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.P_FIELD_LO,
                self.tr("Trường Lô (Chữ)"),
                parentLayerParameterName=self.P_INPUT,
                type=QgsProcessingParameterField.String,
                defaultValue="lo"
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_SELECTED_ONLY, self.tr("Chỉ xử lý đối tượng đang chọn"), defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_CREATE_IF_MISSING, self.tr("Tạo trường 'lo' nếu chưa có"), defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_IN_PLACE, self.tr("Cập nhật vào lớp (in-place)"), defaultValue=False
            )
        )

        self.addParameter(QgsProcessingParameterFeatureSink(self.P_OUTPUT, self.tr("Đầu ra")))

    # ---------- Helpers ----------
    @staticmethod
    def _to_float_or_none(v):
        try:
            x = float(v)
            if math.isfinite(x):
                return x
            return None
        except Exception:
            return None

    @staticmethod
    def _bbox_extrema(g):
        if g is None or g.isEmpty():
            return None, None
        b = g.boundingBox()
        # QgsRectangle may be empty; protect
        try:
            xmin = b.xMinimum()
            ymax = b.yMaximum()
            if math.isfinite(xmin) and math.isfinite(ymax):
                return float(xmin), float(ymax)
            return None, None
        except Exception:
            return None, None

    @staticmethod
    def _centroid_xy(g):
        if g is None or g.isEmpty():
            return None, None
        try:
            pt = g.centroid().asPoint()
            x, y = pt.x(), pt.y()
            if math.isfinite(x) and math.isfinite(y):
                return float(x), float(y)
        except Exception:
            # fallback bbox center
            try:
                b = g.boundingBox()
                x = (b.xMinimum() + b.xMaximum()) / 2.0
                y = (b.yMinimum() + b.yMaximum()) / 2.0
                if math.isfinite(x) and math.isfinite(y):
                    return float(x), float(y)
            except Exception:
                pass
        return None, None

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback):
        src = self.parameterAsSource(parameters, self.P_INPUT, context)
        vl = self.parameterAsVectorLayer(parameters, self.P_INPUT, context)
        if vl is None or not isinstance(vl, QgsVectorLayer):
            raise Exception(self.tr("Không thể truy cập lớp đầu vào dưới dạng QgsVectorLayer."))

        method = self.parameterAsEnum(parameters, self.P_METHOD, context)
        in_place = self.parameterAsBool(parameters, self.P_IN_PLACE, context)

        fld_maxa = self.parameterAsFields(parameters, self.P_FIELD_MAXA, context)[0]
        fld_tk = self.parameterAsFields(parameters, self.P_FIELD_TK, context)[0]
        fld_khoanh = self.parameterAsFields(parameters, self.P_FIELD_KHOANH, context)[0]
        fld_kd = self.parameterAsFields(parameters, self.P_FIELD_KD, context)[0]
        fld_vd = self.parameterAsFields(parameters, self.P_FIELD_VD, context)[0]
        fld_lo = self.parameterAsFields(parameters, self.P_FIELD_LO, context)[0]

        selected_only = self.parameterAsBool(parameters, self.P_SELECTED_ONLY, context)
        create_if_missing = self.parameterAsBool(parameters, self.P_CREATE_IF_MISSING, context)

        # Request theo selection trên CHÍNH layer
        if selected_only:
            sel = vl.selectedFeatureIds()
            if not sel:
                return {self.P_OUTPUT: vl.id()} if in_place else {self.P_OUTPUT: None}
            req = QgsFeatureRequest().setFilterFids(sel)
        else:
            req = QgsFeatureRequest()

        src_fields = src.fields()
        idx_maxa = src_fields.indexFromName(fld_maxa)
        idx_tk = src_fields.indexFromName(fld_tk)
        idx_khoanh = src_fields.indexFromName(fld_khoanh)
        idx_kd_src = src_fields.indexFromName(fld_kd)
        idx_vd_src = src_fields.indexFromName(fld_vd)

        # --- IN-PLACE branch: ensure editable & fields exist ---
        if in_place:
            prov = vl.dataProvider()
            if not (prov.capabilities() & QgsVectorDataProvider.ChangeAttributeValues):
                raise Exception(self.tr("Nguồn dữ liệu không hỗ trợ chỉnh sửa thuộc tính (ChangeAttributeValues)."))

            # đảm bảo có trường lo
            if src_fields.indexFromName(fld_lo) < 0:
                if create_if_missing:
                    if not prov.addAttributes([QgsField(fld_lo, QVariant.String)]):
                        raise Exception(self.tr("Không thể thêm trường 'lo' vào lớp."))
                    vl.updateFields()
                    # refresh src fields
                    src = self.parameterAsSource(parameters, self.P_INPUT, context)
                    src_fields = src.fields()
                else:
                    raise Exception(self.tr("Thiếu trường 'lo'. Hãy bật tuỳ chọn tạo trường."))

            # Lấy lại index trên layer (sau khi có thể đã thêm trường)
            idx_lo = vl.fields().indexFromName(fld_lo)
            idx_kd_inplace = vl.fields().indexFromName(fld_kd)
            idx_vd_inplace = vl.fields().indexFromName(fld_vd)

            for (idx, name) in [(idx_lo, fld_lo), (idx_kd_inplace, fld_kd), (idx_vd_inplace, fld_vd),
                                (idx_maxa, fld_maxa), (idx_tk, fld_tk), (idx_khoanh, fld_khoanh)]:
                if idx < 0:
                    raise Exception(self.tr(f"Không tìm thấy trường '{name}' trong lớp đầu vào."))

        # --- OUTPUT (non in-place) sink branch ---
        else:
            out_fields = QgsFields(src_fields)
            if out_fields.indexFromName(fld_lo) < 0:
                if create_if_missing:
                    out_fields.append(QgsField(fld_lo, QVariant.String))
                else:
                    raise Exception(self.tr("Thiếu trường 'lo'. Hãy bật tuỳ chọn tạo trường."))

            (sink, dest_id) = self.parameterAsSink(
                parameters, self.P_OUTPUT, context, out_fields, src.wkbType(), src.sourceCrs()
            )
            out_idx_lo = out_fields.indexFromName(fld_lo)
            out_idx_kd = out_fields.indexFromName(fld_kd)
            out_idx_vd = out_fields.indexFromName(fld_vd)

        # --- Scan features and group ---
        feats_cache = []  # (fid, attrs, geom, kd_val, vd_val)
        groups = {}

        for f in src.getFeatures(req):
            g = f.geometry()

            if method == self.METHOD_DINH_LO:
                kd_val, vd_val = self._bbox_extrema(g)
            elif method == self.METHOD_TAM_LO:
                kd_val, vd_val = self._centroid_xy(g)
            else:
                kd_val = self._to_float_or_none(f[idx_kd_src])
                vd_val = self._to_float_or_none(f[idx_vd_src])

            feats_cache.append((f.id(), f.attributes(), g, kd_val, vd_val))
            key = (f[idx_maxa], f[idx_tk], f[idx_khoanh])
            groups.setdefault(key, []).append(len(feats_cache) - 1)

        total_groups = max(1, len(groups))
        done = 0

        if in_place:
            if not vl.isEditable() and not vl.startEditing():
                raise Exception(self.tr("Không thể bật chế độ chỉnh sửa in-place."))

        # --- Process each group ---
        for key, idx_list in groups.items():
            if feedback.isCanceled():
                break

            def sort_key(ix):
                _, _, _, kd_val, vd_val = feats_cache[ix]
                vd_key = -(vd_val) if (vd_val is not None) else math.inf
                kd_key = kd_val if (kd_val is not None) else math.inf
                return (vd_key, kd_key)

            idx_list.sort(key=sort_key)

            if in_place:
                # batch changes per group (safer)
                batch = {}
                for i, ix in enumerate(idx_list, start=1):
                    fid, attrs, geom, kd_val, vd_val = feats_cache[ix]
                    changes = {}

                    if method != self.METHOD_TU_DO:
                        changes[idx_kd_inplace] = kd_val  # None -> NULL
                        changes[idx_vd_inplace] = vd_val
                    changes[idx_lo] = str(i)

                    batch[fid] = changes

                if batch:
                    prov = vl.dataProvider()
                if not prov.changeAttributeValues(batch):
                        vl.rollBack()
                        raise Exception(self.tr("Cập nhật thuộc tính in-place thất bại."))
            else:
                for i, ix in enumerate(idx_list, start=1):
                    fid, attrs, geom, kd_val, vd_val = feats_cache[ix]
                    newf = QgsFeature(out_fields)
                    newf.setGeometry(geom)
                    # Extend attrs to out_fields length
                    if len(attrs) < out_fields.count():
                        attrs = attrs + [None] * (out_fields.count() - len(attrs))

                    if method != self.METHOD_TU_DO:
                        attrs[out_idx_kd] = kd_val
                        attrs[out_idx_vd] = vd_val
                    attrs[out_idx_lo] = str(i)
                    newf.setAttributes(attrs)
                    sink.addFeature(newf, QgsFeatureSink.FastInsert)

            done += 1
            feedback.setProgress(int(done * 100 / total_groups))

        if in_place:
            # refresh layer state after provider-level edits
            try:
                vl.triggerRepaint()
            except Exception:
                pass
            return {self.P_OUTPUT: vl.id()}
        else:
            return {self.P_OUTPUT: dest_id}

    def createInstance(self):
        return DienSoHieuLoAlg()
