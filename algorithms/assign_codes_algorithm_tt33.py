# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterString, QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink, QgsProcessingException,
    QgsVectorLayer, QgsFields, QgsField, QgsFeature, QgsCoordinateReferenceSystem
)

def _tr(text):
    return QCoreApplication.translate("AssignCodesAlgorithm33", text)

# ===== MẶC ĐỊNH KHI LDLR BLANK/NULL =====
DEFAULT_MALDLR = 0
DEFAULT_NGGOCR = 3

# ===== BẢNG ÁNH XẠ LDLR -> (maldlr,nggocr) =====
# (Không phân biệt hoa/thường nếu CASE_SENSITIVE=False)
CODE_MAP = {
    "TXG1": (1 ,1), "TXB1": (2 ,1), "RLG1": (3 ,1), "RLB1": (4 ,1),
    "LKG1": (5 ,1), "LKB1": (6 ,1), "RKG1": (7 ,1), "RKB1": (8 ,1),
    "TXDG1": (9 ,1), "TXDB1": (10 ,1), "RNM1": (11 ,1), "RNP1": (12 ,1),
    "RNN1": (13 ,1), "TXG": (14 ,1), "TXB": (15 ,1), "TXN": (16 ,1),
    "TXK": (17 ,1), "TXP": (18 ,1), "RLG": (19 ,1), "RLB": (20 ,1),
    "RLN": (21 ,1), "RLK": (22 ,1), "RLP": (23 ,1), "NRLG": (24 ,1),
    "NRLB": (25 ,1), "NRLN": (26 ,1), "NRLK": (27 ,1), "NRLP": (28 ,1),
    "LKG": (29 ,1), "LKB": (30 ,1), "LKN": (31 ,1), "LKK": (32 ,1),
    "LKP": (33 ,1), "RKG": (34 ,1), "RKB": (35 ,1), "RKN": (36 ,1),
    "RKK": (37 ,1), "RKP": (38 ,1), "TXDG": (39 ,1), "TXDB": (40 ,1),
    "TXDN": (41 ,1), "TXDK": (42 ,1), "TXDP": (43 ,1), "NMG": (44 ,1),
    "NMB": (45 ,1), "NMN": (46 ,1), "NMP": (47 ,1), "NPG": (48 ,1),
    "NPB": (49 ,1), "NPN": (50 ,1), "NPP": (51 ,1), "NN": (52 ,1),
    "TLU": (53 ,1), "NUA": (54 ,1), "VAU": (55 ,1), "LOO": (56 ,1),
    "TNK": (57 ,1), "TND": (58 ,1), "HG1": (59 ,1), "HG2": (60 ,1),
    "HGD": (61 ,1), "CD": (62 ,1), "CDD": (63 ,1), "CDN": (64 ,1),
    "RTG": (65 ,2), "RTGD": (66 ,2), "RTM": (67 ,2), "RTP": (68 ,2),
    "RTC": (69 ,2), "RTTN": (70 ,2), "RTTND": (71 ,2), "RTCD": (72 ,2),
    "RTCDN": (73 ,2), "RTCDC": (74 ,2), "RTK": (75 ,2), "RTKD": (76 ,2),
    "DT2": (83 ,3), "DT2D": (84 ,3), "DT2M": (85 ,3), "DT2P": (86 ,3),
    "DTR": (77 ,3), "DTRD": (78 ,3), "DTRM": (79 ,3), "DTRP": (80 ,3),
    "DTRN": (81 ,3), "DTRC": (82 ,3), "DT1": (87 ,3), "DT1D": (88 ,3),
    "DT1M": (89 ,3), "DT1P": (90 ,3), "BC1": (91 ,3), "BC2": (92 ,3),
    "DNN": (93 ,3), "NND": (94 ,3), "NNM": (95 ,3), "NNP": (96 ,3),
    "MN": (97 ,3), "DK": (98 ,3), "DKH": (98 ,3),
}

class AssignCodesAlgorithm33(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    FIELD_LDLR = "FIELD_LDLR"
    FIELD_MALDLR = "FIELD_MALDLR"
    FIELD_NGGOCR = "FIELD_NGGOCR"
    CASE_SENSITIVE = "CASE_SENSITIVE"
    CREATE_MISSING_FIELDS = "CREATE_MISSING_FIELDS"
    IN_PLACE = "IN_PLACE"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate('AssignCodes', text)
    def name(self):
        return "assign_maldlr_nggocr_by_ldlr"

    def displayName(self):
        return _tr("Chuẩn hoá maldlr và nggocr theo ldlr (TT33)")
    def group(self):
        return self.tr('Tiện ích trường')
    def groupId(self):
        return 'field_utils'
    def shortHelpString(self):
        return _tr(
            "Trong shapefile của bạn có trường ldlr ghi ký hiệu trạng thái rừng theo Thông tư số 33/2023/TT-BNNPTNT.\n"
            "Hệ thống sẽ dựa vào ký hiệu trong trường ldlr để gán mã trạng thái rừng cho trường maldlr và nguồn gốc rừng cho trường nggocr theo đúng quy định của Thông tư số 33/2023/TT-BNNPTNT.\n"
            f"Nếu ldlr rỗng/Null: gán mặc định maldlr={DEFAULT_MALDLR}, nggocr={DEFAULT_NGGOCR}.\n"
            "Giữ nguyên tất cả trường còn lại. Có thể cập nhật in-place hoặc ghi ra lớp mới.\n"
            "Nếu thiếu trường maldlr/nggocr, bạn có thể chọn tự thêm trường kiểu Integer."
        )

    def createInstance(self):
        return AssignCodesAlgorithm33()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, _tr("Lớp đầu vào"), [QgsProcessing.TypeVectorAnyGeometry]
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_LDLR, _tr("Trường ldlr"), defaultValue="ldlr"
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_MALDLR, _tr("Trường maldlr (Int)"), defaultValue="maldlr"
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_NGGOCR, _tr("Trường nggocr (Int)"), defaultValue="nggocr"
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.CASE_SENSITIVE, _tr("Phân biệt hoa/thường?"), defaultValue=False
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.CREATE_MISSING_FIELDS, _tr("Tự thêm maldlr/nggocr nếu thiếu"), defaultValue=True
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.IN_PLACE, _tr("Cập nhật trực tiếp (in-place) lớp đầu vào"), defaultValue=True
        ))
        # SINK chuẩn Processing: hoạt động cho cả 3.16 & 3.44, kể cả TEMPORARY_OUTPUT
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, _tr("Lớp đầu ra (tắt in-place để dùng)"),
            type=QgsProcessing.TypeVectorAnyGeometry, optional=True
        ))

    @staticmethod
    def _is_blank(val):
        if val is None:
            return True
        if isinstance(val, str):
            return val.strip() == ""
        return False

    @staticmethod
    def _norm_code(val):
        """Chuẩn hoá ldlr để tra cứu: strip + upper (không thay ký tự)."""
        if val is None:
            return None
        if isinstance(val, str):
            s = val.strip()
            return s.upper() if s else None
        # Nếu là số/vật khác -> ép chuỗi rồi chuẩn hoá
        s = str(val).strip()
        return s.upper() if s else None

    def processAlgorithm(self, parameters, context, feedback):
        src = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None:
            raise QgsProcessingException("Không đọc được lớp đầu vào")

        in_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if not isinstance(in_layer, QgsVectorLayer) or not in_layer.isValid():
            raise QgsProcessingException("Lớp đầu vào không hợp lệ")

        fld_ldlr   = self.parameterAsString(parameters, self.FIELD_LDLR, context) or "ldlr"
        fld_maldlr = self.parameterAsString(parameters, self.FIELD_MALDLR, context) or "maldlr"
        fld_nggocr = self.parameterAsString(parameters, self.FIELD_NGGOCR, context) or "nggocr"
        case_sensitive = self.parameterAsBoolean(parameters, self.CASE_SENSITIVE, context)
        create_missing = self.parameterAsBoolean(parameters, self.CREATE_MISSING_FIELDS, context)
        in_place = self.parameterAsBoolean(parameters, self.IN_PLACE, context)

        fields = in_layer.fields()
        idx_ldlr   = fields.indexFromName(fld_ldlr)
        idx_maldlr = fields.indexFromName(fld_maldlr)
        idx_nggocr = fields.indexFromName(fld_nggocr)

        # ---------- Chuẩn bị lookup ----------
        if case_sensitive:
            lookup = dict(CODE_MAP)  # dùng nguyên khoá
            def kfunc(x): return x  # không đổi
        else:
            # khoá là UPPER, tra cứu bằng _norm_code
            lookup = {k.upper(): v for k, v in CODE_MAP.items()}
            def kfunc(x): return self._norm_code(x)

        # ---------- Nhánh IN-PLACE ----------
        if in_place:
            # (1) Bổ sung field còn thiếu TRÊN LỚP GỐC khi in-place
            if create_missing:
                to_add = []
                if idx_maldlr < 0:
                    to_add.append(QgsField(fld_maldlr, QVariant.Int))
                if idx_nggocr < 0:
                    to_add.append(QgsField(fld_nggocr, QVariant.Int))
                if to_add:
                    in_layer.startEditing()
                    if not in_layer.dataProvider().addAttributes(to_add):
                        in_layer.rollBack()
                        raise QgsProcessingException("Không thể thêm trường mới")
                    in_layer.updateFields()
                    fields = in_layer.fields()
                    idx_maldlr = fields.indexFromName(fld_maldlr)
                    idx_nggocr = fields.indexFromName(fld_nggocr)

            # (2) Kiểm tra chỉ số
            if idx_ldlr < 0:
                raise QgsProcessingException(f"Không tìm thấy trường '{fld_ldlr}'")
            if idx_maldlr < 0:
                raise QgsProcessingException(f"Không tìm thấy trường '{fld_maldlr}'")
            if idx_nggocr < 0:
                raise QgsProcessingException(f"Không tìm thấy trường '{fld_nggocr}'")

            # (3) Cập nhật
            prov = in_layer.dataProvider()
            if not in_layer.isEditable():
                in_layer.startEditing()
            in_layer.beginEditCommand(_tr("Gán maldlr/nggocr theo ldlr"))

            changes = {}
            total = src.featureCount() or 1
            for i, f in enumerate(in_layer.getFeatures(), start=1):
                if i % 500 == 0:
                    feedback.setProgress(int(100.0 * i / total))

                code_raw = f[idx_ldlr]
                if self._is_blank(code_raw):
                    mal_val, ng_val = DEFAULT_MALDLR, DEFAULT_NGGOCR
                else:
                    key = code_raw if case_sensitive else kfunc(code_raw)
                    pair = lookup.get(key) if key is not None else None
                    if not pair:
                        # Không match → giữ nguyên
                        continue
                    mal_val, ng_val = pair

                updates = {}
                if f[idx_maldlr] != mal_val:
                    updates[idx_maldlr] = int(mal_val)
                if f[idx_nggocr] != ng_val:
                    updates[idx_nggocr] = int(ng_val)
                if updates:
                    changes[f.id()] = updates

            if changes:
                ok = prov.changeAttributeValues(changes)
                if not ok:
                    in_layer.destroyEditCommand()
                    in_layer.rollBack()
                    raise QgsProcessingException("changeAttributeValues() trả về False")

            in_layer.endEditCommand()
            if not in_layer.commitChanges():
                in_layer.rollBack()
                raise QgsProcessingException("Không commit được thay đổi thuộc tính")

            # Trả về chính lớp nguồn (QGIS sẽ refresh)
            return {self.OUTPUT: in_layer.source()}

        # ---------- Nhánh XUẤT LỚP MỚI ----------
        # Không sửa lớp gốc; nếu thiếu field, ta thêm vào schema OUTPUT.
        out_fields = QgsFields(fields)
        # Nếu thiếu, thêm field ngay trên output schema
        if out_fields.indexFromName(fld_maldlr) < 0:
            out_fields.append(QgsField(fld_maldlr, QVariant.Int))
        if out_fields.indexFromName(fld_nggocr) < 0:
            out_fields.append(QgsField(fld_nggocr, QVariant.Int))

        # Cập nhật lại chỉ số theo schema output
        idx_ldlr_out   = out_fields.indexFromName(fld_ldlr)
        idx_maldlr_out = out_fields.indexFromName(fld_maldlr)
        idx_nggocr_out = out_fields.indexFromName(fld_nggocr)

        if idx_ldlr_out < 0:
            raise QgsProcessingException(f"Không tìm thấy trường '{fld_ldlr}'")
        if idx_maldlr_out < 0:
            raise QgsProcessingException(f"Không tạo được trường '{fld_maldlr}' cho output")
        if idx_nggocr_out < 0:
            raise QgsProcessingException(f"Không tạo được trường '{fld_nggocr}' cho output")

        sink, sink_id = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            out_fields, src.wkbType(),
            in_layer.sourceCrs() if in_layer.sourceCrs().isValid() else QgsCoordinateReferenceSystem()
        )
        if sink is None:
            raise QgsProcessingException("Không tạo được lớp đầu ra (sink)")

        total = src.featureCount() or 1
        for i, f in enumerate(in_layer.getFeatures(), start=1):
            if i % 500 == 0:
                feedback.setProgress(int(100.0 * i / total))

            attrs = list(f.attributes())
            # Bổ sung chỗ trống nếu schema output dài hơn
            if len(attrs) < out_fields.count():
                attrs += [None] * (out_fields.count() - len(attrs))

            code_raw = attrs[idx_ldlr_out]
            if self._is_blank(code_raw):
                mal_val, ng_val = DEFAULT_MALDLR, DEFAULT_NGGOCR
                attrs[idx_maldlr_out] = int(mal_val)
                attrs[idx_nggocr_out] = int(ng_val)
            else:
                key = code_raw if case_sensitive else kfunc(code_raw)
                pair = lookup.get(key) if key is not None else None
                if pair:
                    mal_val, ng_val = pair
                    attrs[idx_maldlr_out] = int(mal_val)
                    attrs[idx_nggocr_out] = int(ng_val)
                # else: không match → để nguyên giá trị (có thể None nếu field mới)

            new_f = QgsFeature(out_fields)
            new_f.setAttributes(attrs)
            new_f.setGeometry(f.geometry())
            sink.addFeature(new_f)

        return {self.OUTPUT: sink_id}
