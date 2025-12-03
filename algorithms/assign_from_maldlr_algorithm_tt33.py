# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterString, QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink, QgsProcessingException,
    QgsVectorLayer, QgsFields, QgsField, QgsFeature, QgsCoordinateReferenceSystem
)

def _tr(text):
    return QCoreApplication.translate("AssignFromMaldlrAlgorithm33", text)

# ===== BẢNG ÁNH XẠ: maldlr(int) -> (ldlr:str, nggocr:int) =====
MALDLR_MAP = {
    1: ("TXG1",1), 2: ("TXB1",1), 3: ("RLG1",1), 4: ("RLB1",1), 5: ("LKG1",1), 6: ("LKB1",1),
    7: ("RKG1",1), 8: ("RKB1",1), 9: ("TXDG1",1), 10: ("TXDB1",1), 11: ("RNM1",1), 12: ("RNP1",1),
    13: ("RNN1",1), 14: ("TXG",1), 15: ("TXB",1), 16: ("TXN",1), 17: ("TXK",1), 18: ("TXP",1),
    19: ("RLG",1), 20: ("RLB",1), 21: ("RLN",1), 22: ("RLK",1), 23: ("RLP",1), 24: ("NRLG",1),
    25: ("NRLB",1), 26: ("NRLN",1), 27: ("NRLK",1), 28: ("NRLP",1), 29: ("LKG",1), 30: ("LKB",1),
    31: ("LKN",1), 32: ("LKK",1), 33: ("LKP",1), 34: ("RKG",1), 35: ("RKB",1), 36: ("RKN",1),
    37: ("RKK",1), 38: ("RKP",1), 39: ("TXDG",1), 40: ("TXDB",1), 41: ("TXDN",1), 42: ("TXDK",1),
    43: ("TXDP",1), 44: ("NMG",1), 45: ("NMB",1), 46: ("NMN",1), 47: ("NMP",1), 48: ("NPG",1),
    49: ("NPB",1), 50: ("NPN",1), 51: ("NPP",1), 52: ("NN",1), 53: ("TLU",1), 54: ("NUA",1),
    55: ("VAU",1), 56: ("LOO",1), 57: ("TNK",1), 58: ("TND",1), 59: ("HG1",1), 60: ("HG2",1),
    61: ("HGD",1), 62: ("CD",1), 63: ("CDD",1), 64: ("CDN",1),
    65: ("RTG",2), 66: ("RTGD",2), 67: ("RTM",2), 68: ("RTP",2), 69: ("RTC",2), 70: ("RTTN",2),
    71: ("RTTND",2), 72: ("RTCD",2), 73: ("RTCDN",2), 74: ("RTCDC",2), 75: ("RTK",2), 76: ("RTKD",2),
    83: ("DT2",3), 84: ("DT2D",3), 85: ("DT2M",3), 86: ("DT2P",3), 77: ("DTR",3), 78: ("DTRD",3),
    79: ("DTRM",3), 80: ("DTRP",3), 81: ("DTRN",3), 82: ("DTRC",3), 87: ("DT1",3), 88: ("DT1D",3),
    89: ("DT1M",3), 90: ("DT1P",3), 91: ("BC1",3), 92: ("BC2",3), 93: ("DNN",3), 94: ("NND",3),
    95: ("NNM",3), 96: ("NNP",3), 97: ("MN",3), 98: ("DK",3),
}

# Độ dài gợi ý cho ldlr khi cần tạo mới (DBF ≤ 254, 20 là đủ cho các mã TT33)
LDLR_LEN_HINT = 20

class AssignFromMaldlrAlgorithm33(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    FIELD_MALDLR = "FIELD_MALDLR"
    FIELD_LDLR = "FIELD_LDLR"
    FIELD_NGGOCR = "FIELD_NGGOCR"
    CREATE_MISSING = "CREATE_MISSING_FIELDS"
    IN_PLACE = "IN_PLACE"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate('AssignFromMaldlr', text)
    def createInstance(self): return AssignFromMaldlrAlgorithm33()
    def name(self): return "assign_ldlr_nggocr_from_maldlr"
    def displayName(self): return _tr("Chuẩn hoá ldlr và nggocr theo maldlr (TT33)")
    def group(self):
        return self.tr('Tiện ích trường')
    def groupId(self):
        return 'field_utils'
    def shortHelpString(self):
        return _tr(
            "Trong shapefile của bạn có trường maldlr ghi ký hiệu trạng thái rừng theo Thông tư số 33/2023/TT-BNNPTNT. Hệ thống sẽ dựa vào ký hiệu trong trường maldlr để gán ký hiệu trạng thái rừng cho trường ldlr và nguồn gốc rừng cho trường nggocr theo đúng quy định của Thông tư số 33/2023/TT-BNNPTNT.\n"
            "• In-place: cập nhật trực tiếp lớp đầu vào (mặc định)\n"
            "• Không In-place: ghi ra lớp mới (OUTPUT)\n"
            "• Nếu thiếu ldlr/nggocr có thể tự thêm (Integer/String)."
        )
    

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, _tr("Lớp đầu vào"), [QgsProcessing.TypeVectorAnyGeometry]
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_MALDLR, _tr("Trường maldlr (Int)"), defaultValue="maldlr"
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_LDLR, _tr("Trường ldlr (String)"), defaultValue="ldlr"
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_NGGOCR, _tr("Trường nggocr (Int)"), defaultValue="nggocr"
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.CREATE_MISSING, _tr("Tự thêm ldlr/nggocr nếu thiếu"), defaultValue=True
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.IN_PLACE, _tr("Cập nhật trực tiếp (in-place) lớp đầu vào"), defaultValue=True
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, _tr("Lớp đầu ra (nếu không in-place)"),
            type=QgsProcessing.TypeVectorAnyGeometry, optional=True
        ))

    # ---- ép kiểu int an toàn cho maldlr
    @staticmethod
    def _to_int_safe(val):
        if val is None:
            return None
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            try:
                return int(val)
            except Exception:
                return None
        s = str(val).strip()
        if s == "":
            return None
        try:
            return int(s)
        except Exception:
            try:
                return int(float(s))
            except Exception:
                return None

    def processAlgorithm(self, parameters, context, feedback):
        src = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None:
            raise QgsProcessingException("Không đọc được lớp đầu vào")

        in_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if not isinstance(in_layer, QgsVectorLayer) or not in_layer.isValid():
            raise QgsProcessingException("Lớp đầu vào không hợp lệ")

        fld_maldlr = self.parameterAsString(parameters, self.FIELD_MALDLR, context) or "maldlr"
        fld_ldlr   = self.parameterAsString(parameters, self.FIELD_LDLR, context) or "ldlr"
        fld_nggocr = self.parameterAsString(parameters, self.FIELD_NGGOCR, context) or "nggocr"
        create_missing = self.parameterAsBoolean(parameters, self.CREATE_MISSING, context)
        #in_place_param = self.parameterAsBoolean(parameters, self.IN_PLACE, context)

        # Nếu người dùng chọn OUTPUT, ưu tiên ghi ra lớp mới
        #out_defined = parameters.get(self.OUTPUT) not in (None, "", False)
        #in_place = in_place_param and not out_defined
        in_place = self.parameterAsBoolean(parameters, self.IN_PLACE, context)

        fields = in_layer.fields()
        idx_maldlr = fields.indexFromName(fld_maldlr)
        idx_ldlr   = fields.indexFromName(fld_ldlr)
        idx_nggocr = fields.indexFromName(fld_nggocr)

        if idx_maldlr < 0:
            raise QgsProcessingException(f"Không tìm thấy trường '{fld_maldlr}'")

        # ================= IN-PLACE =================
        if in_place:
            # Thêm field còn thiếu trên lớp gốc (nếu được phép)
            if create_missing:
                to_add = []
                if idx_ldlr < 0:
                    to_add.append(QgsField(fld_ldlr, QVariant.String, len=LDLR_LEN_HINT))
                if idx_nggocr < 0:
                    to_add.append(QgsField(fld_nggocr, QVariant.Int))
                if to_add:
                    in_layer.startEditing()
                    if not in_layer.dataProvider().addAttributes(to_add):
                        in_layer.rollBack()
                        raise QgsProcessingException("Không thể thêm trường mới")
                    in_layer.updateFields()
                    fields = in_layer.fields()
                    idx_ldlr   = fields.indexFromName(fld_ldlr)
                    idx_nggocr = fields.indexFromName(fld_nggocr)

            # Kiểm tra chỉ số
            if idx_ldlr < 0 or idx_nggocr < 0:
                raise QgsProcessingException("Thiếu trường ldlr/nggocr. Hãy bật 'Tự thêm...' hoặc tạo thủ công.")

            prov = in_layer.dataProvider()
            if not in_layer.isEditable():
                in_layer.startEditing()
            in_layer.beginEditCommand(_tr("Gán ldlr & nggocr theo maldlr (in-place)"))

            changes = {}
            batch = 0
            BATCH_SIZE = 5000  # tối ưu RAM: đẩy định kỳ theo lô
            total = src.featureCount() or 1

            for i, f in enumerate(in_layer.getFeatures(), start=1):
                if i % 500 == 0:
                    feedback.setProgress(int(100.0 * i / total))

                mal_key = self._to_int_safe(f[idx_maldlr])
                pair = MALDLR_MAP.get(mal_key) if mal_key is not None else None
                if not pair:
                    continue  # không match thì bỏ qua (giữ nguyên)

                ldlr_val, ng_val = pair
                updates = {}
                if f[idx_ldlr] != ldlr_val:
                    updates[idx_ldlr] = ldlr_val
                if f[idx_nggocr] != ng_val:
                    updates[idx_nggocr] = int(ng_val)
                if updates:
                    changes[f.id()] = updates
                    batch += 1

                # Đẩy theo lô để tránh dict quá lớn
                if batch >= BATCH_SIZE:
                    if not prov.changeAttributeValues(changes):
                        in_layer.destroyEditCommand()
                        in_layer.rollBack()
                        raise QgsProcessingException("changeAttributeValues() trả về False (lô)")
                    changes.clear()
                    batch = 0

            # Đẩy phần còn lại
            if changes:
                if not prov.changeAttributeValues(changes):
                    in_layer.destroyEditCommand()
                    in_layer.rollBack()
                    raise QgsProcessingException("changeAttributeValues() trả về False")

            in_layer.endEditCommand()
            if not in_layer.commitChanges():
                in_layer.rollBack()
                raise QgsProcessingException("Không commit được thay đổi thuộc tính")

            in_layer.triggerRepaint()
            
            return {self.OUTPUT: in_layer.source()}

        # ================= XUẤT LỚP MỚI =================
        # Schema output: giữ toàn bộ field gốc; nếu thiếu ldlr/nggocr và create_missing=True thì thêm.
        out_fields = QgsFields(fields)
        if create_missing:
            if idx_ldlr < 0:
                out_fields.append(QgsField(fld_ldlr, QVariant.String, len=LDLR_LEN_HINT))
                idx_ldlr = out_fields.indexFromName(fld_ldlr)
            if idx_nggocr < 0:
                out_fields.append(QgsField(fld_nggocr, QVariant.Int))
                idx_nggocr = out_fields.indexFromName(fld_nggocr)
        else:
            if idx_ldlr < 0 or idx_nggocr < 0:
                raise QgsProcessingException("Thiếu trường ldlr/nggocr. Bật 'Tự thêm...' hoặc tạo trước.")

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
            # nếu đã thêm field mới trên output, đảm bảo attrs đủ chiều dài
            if len(attrs) < out_fields.count():
                attrs.extend([None] * (out_fields.count() - len(attrs)))

            # Lấy lại index đúng theo out_fields (tránh lệch)
            idx_maldlr_out = out_fields.indexFromName(fld_maldlr)
            idx_ldlr_out   = out_fields.indexFromName(fld_ldlr)
            idx_nggocr_out = out_fields.indexFromName(fld_nggocr)

            mal_key = self._to_int_safe(attrs[idx_maldlr_out] if idx_maldlr_out >= 0 else None)
            pair = MALDLR_MAP.get(mal_key) if mal_key is not None else None
            if pair:
                ldlr_val, ng_val = pair
                if idx_ldlr_out >= 0:
                    attrs[idx_ldlr_out] = ldlr_val
                if idx_nggocr_out >= 0:
                    attrs[idx_nggocr_out] = int(ng_val)

            new_f = QgsFeature(out_fields)
            new_f.setAttributes(attrs)
            new_f.setGeometry(f.geometry())
            sink.addFeature(new_f)

        return {self.OUTPUT: sink_id}
