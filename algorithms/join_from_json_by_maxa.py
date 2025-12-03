# -*- coding: utf-8 -*-
import json
import os
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterString, QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink, QgsProcessingException,
    QgsVectorLayer, QgsFields, QgsField, QgsFeature, QgsCoordinateReferenceSystem
)

def _tr(s):
    return QCoreApplication.translate("JoinFromJsonByMaxa", s)

class JoinFromJsonByMaxa(QgsProcessingAlgorithm):
    # Tham số
    INPUT = "INPUT"
    FIELD_MAXA = "FIELD_MAXA"
    FIELD_MATINHMOI = "FIELD_MATINHMOI"
    FIELD_TINHMOI = "FIELD_TINHMOI"
    FIELD_MAXAMOI = "FIELD_MAXAMOI"
    FIELD_XAMOI = "FIELD_XAMOI"
    IN_PLACE = "IN_PLACE"
    OUTPUT = "OUTPUT"

    # ---- Boilerplate ----
    def tr(self, text):
        return QCoreApplication.translate('JoinFromJson', text)
    def name(self):
        return "join_from_json_by_maxa"

    def displayName(self):
        return _tr("Cập nhật đơn vị hành chính 2 cấp")
    def group(self):
        return self.tr('Tiện ích trường')
    def groupId(self):
        return 'field_utils'
    def shortHelpString(self):
        return _tr(
            "Dựa vào maxa (cũ) theo số nguyên có sẵn trong bản đồ, để cập nhật mã và tên đơn vị hành chính 2 cấp, "
            "và ghi các trường: matinhmoi (Int), tinhmoi (String), maxamoi (Int), xamoi (String).\n"
            "Thuật toán tự tạo các trường đích nếu thiếu. Hỗ trợ cập nhật in-place hoặc ghi ra lớp mới.\n"
            "Một số xã cũ trong bản đồ đã thay đổi mã xã trong quá trình sáp nhập từ năm 2020 đến trước tháng 7/2025 "
            "hoặc xã cũ bị chia tách để sáp nhập vào nhiều xã khác nhau sẽ không cập nhật được mã và tên đơn vị hành chính 2 cấp, "
            "bạn cần phải mở bản đồ ranh giới xã mới ra để kiểm tra và cập nhật thủ công"
        )

    def createInstance(self):
        return JoinFromJsonByMaxa()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, _tr("Lớp đầu vào"), [QgsProcessing.TypeVectorAnyGeometry]
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_MAXA, _tr("Tên trường maxa (trên lớp)"), defaultValue="maxa"
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_MATINHMOI, _tr("Trường matinhmoi (Int)"), defaultValue="matinhmoi"
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_TINHMOI, _tr("Trường tinhmoi (String)"), defaultValue="tinhmoi"
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_MAXAMOI, _tr("Trường maxamoi (Int)"), defaultValue="maxamoi"
        ))
        self.addParameter(QgsProcessingParameterString(
            self.FIELD_XAMOI, _tr("Trường xamoi (String)"), defaultValue="xamoi"
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.IN_PLACE, _tr("Cập nhật trực tiếp (in-place) lớp đầu vào"), defaultValue=True
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, _tr("Lớp đầu ra (nếu không in-place)"),
            type=QgsProcessing.TypeVectorAnyGeometry, optional=True
        ))

    # ---- Tiện ích ép kiểu & khóa so khớp ----
    @staticmethod
    def _to_int_safe(v):
        """Ép về int (chấp nhận int/float/chuỗi số kể cả '65.0'), lỗi -> None."""
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            try:
                return int(v)
            except Exception:
                return None
        s = str(v).strip()
        if s == "":
            return None
        try:
            return int(s)
        except Exception:
            try:
                return int(float(s))
            except Exception:
                return None

    def _key(self, v):
        """Khóa so khớp maxa/maxacu: trả về int hoặc None."""
        return self._to_int_safe(v)

    # ---- Xác định đường dẫn JSON trong plugin ----
    def _resolve_json_path(self):
        # File này nằm tại: <plugin_root>/algorithms/join_from_json_by_maxa.py
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(plugin_root, "data", "dsxa.json"),
            os.path.join(plugin_root, ".data", "dsxa.json"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        raise QgsProcessingException(
            _tr("Không tìm thấy dsxa.json. Đã thử các đường dẫn: ") + "\n" + "\n".join(candidates)
        )

    # ---- Đọc JSON thành bảng tra, CHỈ nhận phanloai ∈ {1,2} ----
    def _load_lookup(self, json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        allowed = {1, 2}
        lookup = {}

        def accept_row(row):
            pl = self._to_int_safe(row.get("phanloai"))
            return pl in allowed

        # Dạng list các object
        if isinstance(data, list):
            for row in data:
                if not isinstance(row, dict):
                    continue
                if not accept_row(row):
                    continue
                k = self._key(row.get("maxacu"))
                if not k:
                    continue
                lookup[k] = {
                    "matinhmoi": self._to_int_safe(row.get("matinhmoi")),
                    "tinhmoi":   row.get("tinhmoi"),
                    "maxamoi":   self._to_int_safe(row.get("maxamoi")),
                    "xamoi":     row.get("xamoi"),
                }
        # Dạng dict: {maxacu: {...}}
        elif isinstance(data, dict):
            for k_raw, row in data.items():
                if not isinstance(row, dict):
                    continue
                if not accept_row(row):
                    continue
                k = self._key(k_raw)
                if not k:
                    continue
                lookup[k] = {
                    "matinhmoi": self._to_int_safe(row.get("matinhmoi")),
                    "tinhmoi":   row.get("tinhmoi"),
                    "maxamoi":   self._to_int_safe(row.get("maxamoi")),
                    "xamoi":     row.get("xamoi"),
                }
        else:
            raise QgsProcessingException(_tr("Cấu trúc JSON không hợp lệ (phải là list hoặc dict)."))

        return lookup

    # ---- Xử lý chính ----
    def processAlgorithm(self, parameters, context, feedback):
        src = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None:
            raise QgsProcessingException("Không đọc được lớp đầu vào")

        in_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if not isinstance(in_layer, QgsVectorLayer) or not in_layer.isValid():
            raise QgsProcessingException("Lớp đầu vào không hợp lệ")

        fld_maxa  = self.parameterAsString(parameters, self.FIELD_MAXA, context) or "maxa"
        fld_matinhmoi = self.parameterAsString(parameters, self.FIELD_MATINHMOI, context) or "matinhmoi"
        fld_tinhmoi   = self.parameterAsString(parameters, self.FIELD_TINHMOI, context)   or "tinhmoi"
        fld_maxamoi   = self.parameterAsString(parameters, self.FIELD_MAXAMOI, context)   or "maxamoi"
        fld_xamoi     = self.parameterAsString(parameters, self.FIELD_XAMOI, context)     or "xamoi"
        in_place = self.parameterAsBoolean(parameters, self.IN_PLACE, context)


        # Tự tìm file JSON
        json_path = self._resolve_json_path()
        #feedback.pushInfo(_tr("Đọc JSON từ: ") + json_path)

        # Tải lookup (chỉ phanloai 1 hoặc 2)
        lookup = self._load_lookup(json_path)
        if not lookup:
            feedback.pushInfo(_tr("Cảnh báo: không có bản ghi JSON hợp lệ (phanloai 1 hoặc 2)."))

        # Kiểm tra & tự tạo field đích nếu thiếu
        fields = in_layer.fields()
        idx_maxa = fields.indexFromName(fld_maxa)
        if idx_maxa < 0:
            raise QgsProcessingException(f"Không tìm thấy trường '{fld_maxa}' trên lớp.")

        idx_matinhmoi = fields.indexFromName(fld_matinhmoi)
        idx_tinhmoi   = fields.indexFromName(fld_tinhmoi)
        idx_maxamoi   = fields.indexFromName(fld_maxamoi)
        idx_xamoi     = fields.indexFromName(fld_xamoi)

        need_add = []
        if idx_matinhmoi < 0: need_add.append(QgsField(fld_matinhmoi, QVariant.Int))
        if idx_tinhmoi   < 0: need_add.append(QgsField(fld_tinhmoi,   QVariant.String, len=80))
        if idx_maxamoi   < 0: need_add.append(QgsField(fld_maxamoi,   QVariant.Int))
        if idx_xamoi     < 0: need_add.append(QgsField(fld_xamoi,     QVariant.String, len=80))

        if need_add:
            in_layer.startEditing()
            if not in_layer.dataProvider().addAttributes(need_add):
                in_layer.rollBack()
                raise QgsProcessingException("Không thể thêm các trường đích.")
            in_layer.updateFields()
            fields = in_layer.fields()
            idx_matinhmoi = fields.indexFromName(fld_matinhmoi)
            idx_tinhmoi   = fields.indexFromName(fld_tinhmoi)
            idx_maxamoi   = fields.indexFromName(fld_maxamoi)
            idx_xamoi     = fields.indexFromName(fld_xamoi)

        # Khẳng định lại chỉ số
        for name, idx in [(fld_matinhmoi, idx_matinhmoi), (fld_tinhmoi, idx_tinhmoi),
                          (fld_maxamoi, idx_maxamoi), (fld_xamoi, idx_xamoi)]:
            if idx < 0:
                raise QgsProcessingException(f"Thiếu trường '{name}' sau khi tạo tự động.")

        # ---- Nhánh in-place ----
        if in_place:
            prov = in_layer.dataProvider()
            if not in_layer.isEditable():
                in_layer.startEditing()
            in_layer.beginEditCommand(_tr("Bổ sung từ JSON theo maxa=maxacu (phanloai 1,2)"))

            changes = {}
            total = src.featureCount()
            done = 0

            for f in in_layer.getFeatures():
                done += 1
                if done % 500 == 0:
                    feedback.setProgress(int(100.0 * done / max(1, total)))

                key = self._key(f[idx_maxa])  # int/None
                if key is None:
                    continue
                row = lookup.get(key)
                if not row:
                    continue

                updates = {}
                if f[idx_matinhmoi] != row.get("matinhmoi"):
                    updates[idx_matinhmoi] = row.get("matinhmoi")
                if f[idx_tinhmoi]   != row.get("tinhmoi"):
                    updates[idx_tinhmoi]   = row.get("tinhmoi")
                if f[idx_maxamoi]   != row.get("maxamoi"):
                    updates[idx_maxamoi]   = row.get("maxamoi")
                if f[idx_xamoi]     != row.get("xamoi"):
                    updates[idx_xamoi]     = row.get("xamoi")

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
                raise QgsProcessingException("Không commit được thay đổi")

            return {self.OUTPUT: in_layer.source()}

        # ---- Nhánh ghi ra lớp mới ----
        out_fields = QgsFields(fields)
        sink, sink_id = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            out_fields, src.wkbType(),
            in_layer.sourceCrs() if in_layer.sourceCrs().isValid() else QgsCoordinateReferenceSystem()
        )
        if sink is None:
            raise QgsProcessingException("Không tạo được lớp đầu ra")

        total = src.featureCount()
        done = 0
        for f in in_layer.getFeatures():
            done += 1
            if done % 500 == 0:
                feedback.setProgress(int(100.0 * done / max(1, total)))

            attrs = list(f.attributes())
            key = self._key(attrs[idx_maxa])  # int/None
            row = lookup.get(key) if key is not None else None
            if row:
                attrs[idx_matinhmoi] = row.get("matinhmoi")  # int
                attrs[idx_tinhmoi]   = row.get("tinhmoi")    # string
                attrs[idx_maxamoi]   = row.get("maxamoi")    # int
                attrs[idx_xamoi]     = row.get("xamoi")      # string

            nf = QgsFeature(out_fields)
            nf.setGeometry(f.geometry())
            nf.setAttributes(attrs)
            sink.addFeature(nf)

        return {self.OUTPUT: sink_id}
