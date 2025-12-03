# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer, QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
    QgsProcessingParameterString, QgsProcessingParameterFolderDestination,
    QgsVectorLayer, QgsProcessingException
)
from qgis import processing
import os
import html

class AlignFieldsToReference(QgsProcessingAlgorithm):
    # Param keys
    P_REF = 'REFERENCE'
    P_TARGETS = 'TARGETS'
    P_ADD_MISSING = 'ADD_MISSING'
    P_EXTRA_MODE = 'EXTRA_MODE'
    P_EXTRA_POS = 'EXTRA_POS'
    P_COERCE = 'COERCE_TYPES'
    P_CASE_SENSITIVE = 'CASE_SENSITIVE'
    P_SUFFIX = 'OUTPUT_SUFFIX'
    P_OUT_FMT = 'OUTPUT_FORMAT'
    P_OUT_DIR = 'OUTPUT_DIR'
    P_FILE_ENC_ENUM = 'FILE_ENCODING_ENUM'  # NEW: drop-list encoding

    EXTRA_KEEP = 0
    EXTRA_REMOVE = 1

    POS_APPEND = 0
    POS_PREPEND = 1

    FMT_GPKG = 0
    FMT_SHP = 1
    FMT_GEOJSON = 2
    FMT_CSV = 3

    # Encoding enum indexes
    ENC_AUTO = 0
    ENC_UTF8 = 1
    ENC_CP1258 = 2       # Vietnamese (Windows)
    ENC_TCVN3 = 3        # (hiếm; thường không dùng với driver hiện đại)
    ENC_CP1252 = 4
    ENC_ISO8859_1 = 5
    ENC_TIS620 = 6       # Thai
    ENC_GBK = 7          # Chinese
    ENC_SHIFT_JIS = 8    # Japanese

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.P_REF, self.tr('Lớp chuẩn (tham chiếu)'),
                [QgsProcessing.TypeVector]
            )
        )
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.P_TARGETS, self.tr('Các lớp cần so sánh/chuẩn hoá'),
                layerType=QgsProcessing.TypeVector
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_ADD_MISSING, self.tr('Tự động thêm các trường thiếu theo lớp chuẩn'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.P_EXTRA_MODE,
                self.tr('Xử lý các trường “thừa”'),
                options=[self.tr('Giữ lại'), self.tr('Xoá bỏ')],
                defaultValue=self.EXTRA_KEEP
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.P_EXTRA_POS,
                self.tr('Vị trí chèn trường “thừa” khi giữ lại'),
                options=[self.tr('Chèn ở cuối (sau các trường chuẩn)'),
                         self.tr('Chèn ở đầu (trước các trường chuẩn)')],
                defaultValue=self.POS_APPEND
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_COERCE,
                self.tr('Cưỡng ép kiểu dữ liệu theo lớp chuẩn (nếu trùng tên nhưng khác kiểu)'),
                defaultValue=True
            )
        )
        # Mặc định KHÔNG phân biệt HOA/thường
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_CASE_SENSITIVE,
                self.tr('Phân biệt HOA/thường khi so khớp tên trường'),
                defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.P_SUFFIX, self.tr('Hậu tố tên file đầu ra'),
                defaultValue='_aligned'
            )
        )
        # Định dạng đầu ra
        self.addParameter(
            QgsProcessingParameterEnum(
                self.P_OUT_FMT, self.tr('Định dạng dữ liệu đầu ra'),
                options=[self.tr('GeoPackage (*.gpkg)'),
                         self.tr('ESRI Shapefile (*.shp)'),
                         self.tr('GeoJSON (*.geojson)'),
                         self.tr('CSV (*.csv)')],
                defaultValue=self.FMT_GPKG
            )
        )
        # Bảng mã: drop-list
        self.addParameter(
            QgsProcessingParameterEnum(
                self.P_FILE_ENC_ENUM, self.tr('Bảng mã xuất'),
                options=[
                    self.tr('Theo lớp đầu vào (Auto)'),
                    'UTF-8',
                    'CP1258',
                    self.tr('TCVN3 (ABC)'),
                    'CP1252',
                    'ISO-8859-1',
                    'TIS-620',
                    'GBK',
                    'Shift_JIS'
                ],
                defaultValue=self.ENC_AUTO
            )
        )
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.P_OUT_DIR, self.tr('Thư mục lưu lớp đã chuẩn hoá')
            )
        )

    def name(self): return 'align_fields_to_reference'
    def displayName(self): return self.tr('Chuẩn hoá cấu trúc trường theo lớp chuẩn')
    def group(self): return self.tr('Tiện ích Trường')
    def groupId(self): return 'field_utils'
    def createInstance(self): return AlignFieldsToReference()
    def tr(self, s): return QCoreApplication.translate('AlignFieldsToReference', s)

    def shortHelpString(self):
        return self.tr(
            "Mục đích:\n"
            "- So sánh và chuẩn hoá cấu trúc trường của nhiều lớp theo một lớp chuẩn.\n"
            "- Sắp xếp tên, thứ tự và kiểu dữ liệu theo lớp chuẩn.\n\n"
            "Điểm chính:\n"
            "- Mặc định KHÔNG phân biệt HOA/thường khi so khớp tên trường.\n"
            "- Chọn định dạng đầu ra (GeoPackage/Shapefile/GeoJSON/CSV).\n"
            "- Chọn bảng mã từ danh sách (có \"Theo lớp đầu vào (Auto)\").\n"
            "- Ghi KẾT QUẢ vào Log (không tạo file HTML).\n\n"
            "Lưu ý:\n"
            "- Khi cưỡng ép kiểu, giá trị không chuyển được sẽ thành NULL.\n"
            "- Shapefile giới hạn 10 ký tự tên trường và kiểu; cân nhắc GeoPackage."
        )

    # ----- Helpers -----
    @staticmethod
    def _field_key(name: str, case_sensitive: bool) -> str:
        return name if case_sensitive else name.lower()

    @staticmethod
    def _qvariant_to_typename(vtype: int) -> str:
        return {
            QVariant.Int: 'Integer',
            QVariant.LongLong: 'Integer64',
            QVariant.Double: 'Real',
            QVariant.String: 'String',
            QVariant.Date: 'Date',
            QVariant.DateTime: 'DateTime',
            QVariant.Time: 'Time',
            QVariant.Bool: 'Boolean'
        }.get(vtype, 'String')

    @staticmethod
    def _quote_field(field_name: str) -> str:
        return '"{}"'.format(field_name.replace('"', '\\"'))

    @staticmethod
    def _fmt_ext(fmt_idx: int) -> str:
        return {0: 'gpkg', 1: 'shp', 2: 'geojson', 3: 'csv'}.get(fmt_idx, 'gpkg')

    @staticmethod
    def _layer_encoding(layer: QgsVectorLayer) -> str:
        enc = None
        prov = layer.dataProvider()
        if hasattr(prov, 'encoding'):
            try:
                enc = prov.encoding()
            except Exception:
                enc = None
        return enc or 'UTF-8'

    def _encoding_from_enum(self, enum_idx: int, input_layer: QgsVectorLayer) -> (str, str):
        # returns (encoding_string, note)
        if enum_idx == self.ENC_AUTO:
            enc = self._layer_encoding(input_layer)
            return enc, enc + ' (Auto theo lớp vào)'
        mapping = {
            self.ENC_UTF8: 'UTF-8',
            self.ENC_CP1258: 'CP1258',
            self.ENC_TCVN3: 'TCVN-ABC',  # chú ý: thường driver không hỗ trợ ABC legacy; chỉ hiển thị cho đủ
            self.ENC_CP1252: 'CP1252',
            self.ENC_ISO8859_1: 'ISO-8859-1',
            self.ENC_TIS620: 'TIS-620',
            self.ENC_GBK: 'GBK',
            self.ENC_SHIFT_JIS: 'Shift_JIS'
        }
        enc = mapping.get(enum_idx, 'UTF-8')
        return enc, enc

    # ----- Main -----
    def processAlgorithm(self, parameters, context, feedback):
        ref_layer: QgsVectorLayer = self.parameterAsVectorLayer(parameters, self.P_REF, context)
        targets = self.parameterAsLayerList(parameters, self.P_TARGETS, context)
        add_missing = self.parameterAsBool(parameters, self.P_ADD_MISSING, context)
        extra_mode = self.parameterAsEnum(parameters, self.P_EXTRA_MODE, context)
        extra_pos = self.parameterAsEnum(parameters, self.P_EXTRA_POS, context)
        coerce = self.parameterAsBool(parameters, self.P_COERCE, context)
        case_sensitive = self.parameterAsBool(parameters, self.P_CASE_SENSITIVE, context)
        suffix = self.parameterAsString(parameters, self.P_SUFFIX, context)
        out_fmt = self.parameterAsEnum(parameters, self.P_OUT_FMT, context)
        out_dir = self.parameterAsFile(parameters, self.P_OUT_DIR, context)
        enc_choice = self.parameterAsEnum(parameters, self.P_FILE_ENC_ENUM, context)

        if not ref_layer or not isinstance(ref_layer, QgsVectorLayer):
            raise QgsProcessingException(self.tr('Lớp chuẩn không hợp lệ.'))
        if not targets:
            raise QgsProcessingException(self.tr('Hãy chọn ít nhất một lớp để so sánh/chuẩn hoá.'))
        if not os.path.isdir(out_dir):
            raise QgsProcessingException(self.tr('Thư mục đầu ra không hợp lệ.'))

        ref_fields = list(ref_layer.fields())
        ref_name_map = {self._field_key(f.name(), case_sensitive): f for f in ref_fields}

        feedback.pushInfo(self.tr('--- BẮT ĐẦU CHUẨN HOÁ TRƯỜNG ---'))
        feedback.pushInfo(self.tr('Lớp chuẩn: {}').format(ref_layer.name()))
        total = len(targets)

        for i, tgt in enumerate(targets, start=1):
            if feedback.isCanceled():
                break
            layer: QgsVectorLayer = tgt
            layer_name = layer.name()
            feedback.pushInfo(self.tr(f'[{i}/{total}] Xử lý lớp: {layer_name}'))

            tgt_fields = list(layer.fields())
            tgt_name_map = {self._field_key(f.name(), case_sensitive): f for f in tgt_fields}

            missing, extra, type_mismatch = [], [], []
            for rf in ref_fields:
                key = self._field_key(rf.name(), case_sensitive)
                if key not in tgt_name_map:
                    missing.append(rf)
                else:
                    tf = tgt_name_map[key]
                    if tf.type() != rf.type():
                        type_mismatch.append((rf.name(), self._qvariant_to_typename(tf.type()), self._qvariant_to_typename(rf.type())))
            for tf in tgt_fields:
                key = self._field_key(tf.name(), case_sensitive)
                if key not in ref_name_map:
                    extra.append(tf)

            # Build mapping
            mapping = []
            def map_entry(out_name, out_type, out_len, out_prec, expr_str):
                return {
                    'name': out_name,
                    'type': out_type,
                    'length': max(0, out_len or 0),
                    'precision': max(0, out_prec or 0),
                    'expression': expr_str or 'NULL'
                }

            for rf in ref_fields:
                rkey = self._field_key(rf.name(), case_sensitive)
                rf_type, rf_len, rf_prec = rf.type(), rf.length(), rf.precision()
                if rkey in tgt_name_map:
                    tf = tgt_name_map[rkey]
                    mapping.append(map_entry(rf.name(), rf_type, rf_len, rf_prec, self._quote_field(tf.name())))
                else:
                    # Nếu không thêm trường thiếu, có thể bỏ cột này; tuy nhiên để đảm bảo khớp cấu trúc,
                    # ta vẫn tạo cột NULL (giữ đúng thứ tự ref).
                    mapping.append(map_entry(rf.name(), rf_type, rf_len, rf_prec, 'NULL'))

            kept_extra = []
            if extra and extra_mode == self.EXTRA_KEEP:
                extra_entries = []
                for tf in extra:
                    extra_entries.append(map_entry(tf.name(), tf.type(), tf.length(), tf.precision(), self._quote_field(tf.name())))
                    kept_extra.append(tf.name())
                mapping = (extra_entries + mapping) if extra_pos == self.POS_PREPEND else (mapping + extra_entries)

            # Step 1: refactor to memory
            tmp_refact = processing.run(
                'native:refactorfields',
                {'INPUT': layer, 'FIELDS_MAPPING': mapping, 'OUTPUT': 'memory:'},
                context=context, feedback=feedback
            )['OUTPUT']

            # Decide encoding for this layer
            file_enc, enc_note = self._encoding_from_enum(enc_choice, layer)

            # Step 2: save to chosen format
            ext = self._fmt_ext(out_fmt)
            safe_name = layer_name.replace(':', '_').replace('/', '_').replace('\\', '_')
            out_path = os.path.join(out_dir, f'{safe_name}{suffix}.{ext}')
            layer_name_out = f'{safe_name}{suffix}'

            save_args = {
                'INPUT': tmp_refact,
                'LAYER_NAME': layer_name_out,
                'DATASOURCE_OPTIONS': [],
                'LAYER_OPTIONS': [],
                'FILE_ENCODING': file_enc,
                'OUTPUT': out_path
            }
            processing.run('native:savefeatures', save_args, context=context, feedback=feedback)

            # ---- LOG SUMMARY FOR THIS LAYER ----
            fmt_note = {self.FMT_GPKG: 'GeoPackage', self.FMT_SHP: 'Shapefile',
                        self.FMT_GEOJSON: 'GeoJSON', self.FMT_CSV: 'CSV'}.get(out_fmt, 'GeoPackage')
            feedback.pushInfo(self.tr('→ Đầu ra: {} | Định dạng: {} | Encoding: {}').format(
                os.path.basename(out_path), fmt_note, enc_note
            ))
            if missing:
                if add_missing:
                    feedback.pushInfo(self.tr('  + THÊM {} trường thiếu: {}').format(
                        len(missing), ', '.join(f.name() for f in missing)))
                else:
                    feedback.pushInfo(self.tr('  ! THIẾU {} trường (điền NULL): {}').format(
                        len(missing), ', '.join(f.name() for f in missing)))
            if type_mismatch:
                if coerce:
                    feedback.pushInfo(self.tr('  ± CƯỠNG ÉP kiểu cho {} trường: {}').format(
                        len(type_mismatch),
                        ', '.join(f'{n} ({t1}→{t2})' for n, t1, t2 in type_mismatch)
                    ))
                else:
                    feedback.pushInfo(self.tr('  ! KHÁC KIỂU {} trường (KHÔNG ép): {}').format(
                        len(type_mismatch),
                        ', '.join(f'{n} ({t1}≠{t2})' for n, t1, t2 in type_mismatch)
                    ))
            if extra:
                if extra_mode == self.EXTRA_REMOVE:
                    feedback.pushInfo(self.tr('  − XOÁ {} trường thừa: {}').format(
                        len(extra), ', '.join(f.name() for f in extra)))
                else:
                    pos_txt = self.tr('sau khối chuẩn') if extra_pos == self.POS_APPEND else self.tr('trước khối chuẩn')
                    feedback.pushInfo(self.tr('  = GIỮ {} trường thừa (chèn {}): {}').format(
                        len(extra), pos_txt, ', '.join(kept_extra)))
            if not missing and not extra and not type_mismatch:
                feedback.pushInfo(self.tr('  = Cấu trúc đã khớp (tái định dạng để giữ thứ tự/kiểu).'))

        feedback.pushInfo(self.tr('--- HOÀN THÀNH ---'))
        return {self.P_OUT_DIR: out_dir}
