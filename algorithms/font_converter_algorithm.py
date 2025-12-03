# -*- coding: utf-8 -*-
"""
QGIS 3.16+ Processing Algorithm (for Provider)
Chuyển đổi bảng mã tiếng Việt giữa Unicode, TCVN3, VNIWin
— bổ sung:
  • Trùng key TCVN3: FIRST-WINS (không còn “toàn chữ HOA” khi TCVN3→Unicode)
  • Ưu tiên khớp chuỗi dài (VNI an toàn)
  • Nhận diện trường chuỗi bằng QVariant.String
  • 'Bỏ dấu (KhongDau)' áp dụng CHO TẤT CẢ chế độ (ra ASCII không dấu)
  • Tùy chọn định dạng chữ: Giữ nguyên / HOA / thường / Hoa đầu câu / Hoa Mỗi Từ
"""

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
    QgsProcessingParameterEnum, QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink, QgsProcessingException,
    QgsFields, QgsFeature
)
import re

# ======================= BẢNG MÃ (như bạn cung cấp; có sửa 1 ký tự trong _KhongDau) =======================
_Unicode = [
u'â',u'Â',u'ă',u'Ă',u'đ',u'Đ',u'ê',u'Ê',u'ô',u'Ô',u'ơ',u'Ơ',u'ư',u'Ư',u'á',u'Á',u'à',u'À',u'ả',u'Ả',u'ã',u'Ã',u'ạ',u'Ạ',
u'ấ',u'Ấ',u'ầ',u'Ầ',u'ẩ',u'Ẩ',u'ẫ',u'Ẫ',u'ậ',u'Ậ',u'ắ',u'Ắ',u'ằ',u'Ằ',u'ẳ',u'Ẳ',u'ẵ',u'Ẵ',u'ặ',u'Ặ',
u'é',u'É',u'è',u'È',u'ẻ',u'Ẻ',u'ẽ',u'Ẽ',u'ẹ',u'Ẹ',u'ế',u'Ế',u'ề',u'Ề',u'ể',u'Ể',u'ễ',u'Ễ',u'ệ',u'Ệ',u'í',u'Í',u'ì',u'Ì',u'ỉ',u'Ỉ',u'ĩ',u'Ĩ',u'ị',u'Ị',
u'ó',u'Ó',u'ò',u'Ò',u'ỏ',u'Ỏ',u'õ',u'Õ',u'ọ',u'Ọ',u'ố',u'Ố',u'ồ',u'Ồ',u'ổ',u'Ổ',u'ỗ',u'Ỗ',u'ộ',u'Ộ',u'ớ',u'Ớ',u'ờ',u'Ờ',u'ở',u'Ở',u'ỡ',u'Ỡ',u'ợ',u'Ợ',
u'ú',u'Ú',u'ù',u'Ù',u'ủ',u'Ủ',u'ũ',u'Ũ',u'ụ',u'Ụ',u'ứ',u'Ứ',u'ừ',u'Ừ',u'ử',u'Ử',u'ữ',u'Ữ',u'ự',u'Ự',u'ỳ',u'Ỳ',u'ỷ',u'Ỷ',u'ỹ',u'Ỹ',u'ỵ',u'Ỵ',u'ý',u'Ý'
]
_TCVN3 = [
u'©',u'¢',u'¨',u'¡',u'®',u'§',u'ª',u'£',u'«',u'¤',u'¬',u'¥',u'­',u'¦',u'¸',u'¸',u'µ',u'µ',u'¶',u'¶',u'·',u'·',u'¹',u'¹',
u'Ê',u'Ê',u'Ç',u'Ç',u'È',u'È',u'É',u'É',u'Ë',u'Ë',u'¾',u'¾',u'»',u'»',u'¼',u'¼',u'½',u'½',u'Æ',u'Æ',
u'Ð',u'Ð',u'Ì',u'Ì',u'Î',u'Î',u'Ï',u'Ï',u'Ñ',u'Ñ',u'Õ',u'Õ',u'Ò',u'Ò',u'Ó',u'Ó',u'Ô',u'Ô',u'Ö',u'Ö',u'Ý',u'Ý',u'×',u'×',u'Ø',u'Ø',u'Ü',u'Ü',u'Þ',u'Þ',
u'ã',u'ã',u'ß',u'ß',u'á',u'á',u'â',u'â',u'ä',u'ä',u'è',u'è',u'å',u'å',u'æ',u'æ',u'ç',u'ç',u'é',u'é',u'í',u'í',u'ê',u'ê',u'ë',u'ë',u'ì',u'ì',u'î',u'î',
u'ó',u'ó',u'ï',u'ï',u'ñ',u'ñ',u'ò',u'ò',u'ô',u'ô',u'ø',u'ø',u'õ',u'õ',u'ö',u'ö',u'÷',u'÷',u'ù',u'ù',u'ú',u'ú',u'û',u'û',u'ü',u'ü',u'þ',u'þ',u'ý',u'ý'
]
_VNIWin = [
u'aâ',u'AÂ',u'aê',u'AÊ',u'ñ',u'Ñ',u'eâ',u'EÂ',u'oâ',u'OÂ',u'ô',u'Ô',u'ö',u'Ö',u'aù',u'AÙ',u'aø',u'AØ',u'aû',u'AÛ',u'aõ',u'AÕ',u'aï',u'AÏ',
u'aá',u'AÁ',u'aà',u'AÀ',u'aå',u'AÅ',u'aã',u'AÃ',u'aä',u'AÄ',u'aé',u'AÉ',u'aè',u'AÈ',u'aú',u'AÚ',u'aü',u'AÜ',u'aë',u'AË',
u'eù',u'EÙ',u'eø',u'EØ',u'eû',u'EÛ',u'eõ',u'EÕ',u'eï',u'EÏ',u'eá',u'EÁ',u'eà',u'EÀ',u'eå',u'EÅ',u'eã',u'EÃ',u'eä',u'EÄ',u'í',u'Í',u'ì',u'Ì',u'æ',u'Æ',u'ó',u'Ó',u'ò',u'Ò',
u'où',u'OÙ',u'oø',u'OØ',u'oû',u'OÛ',u'oõ',u'OÕ',u'oï',u'OÏ',u'oá',u'OÁ',u'oà',u'OÀ',u'oå',u'OÅ',u'oã',u'OÃ',u'oä',u'OÄ',u'ôù',u'ÔÙ',u'ôø',u'ÔØ',u'ôû',u'ÔÛ',u'ôõ',u'ÔÕ',u'ôï',u'ÔÏ',
u'uù',u'UÙ',u'uø',u'UØ',u'uû',u'UÛ',u'uõ',u'UÕ',u'uï',u'UÏ',u'öù',u'ÖÙ',u'öø',u'ÖØ',u'öû',u'ÖÛ',u'öõ',u'ÖÕ',u'öï',u'ÖÏ',u'yø',u'YØ',u'yû',u'YÛ',u'yõ',u'YÕ',u'î',u'Î',u'yù',u'YÙ'
]
# Sửa lỗi gõ nhầm duy nhất trong _KhongDau: 'uE' -> 'E'
_KhongDau = [
u'a',u'A',u'a',u'A',u'd',u'D',u'e',u'E',u'o',u'O',u'o',u'O',u'u',u'U',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',
u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',
u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'i',u'I',u'i',u'I',u'i',u'I',u'i',u'I',u'i',u'I',
u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',
u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'y',u'Y',u'y',u'Y',u'y',u'Y',u'y',u'Y',u'y',u'Y'
]
# ===========================================================================================================

def _compile_regex_map_firstwins(src_list, dst_list):
    """
    Tạo regex + mapping với chính sách FIRST-WINS:
    nếu src trùng, GIỮ ánh xạ lần xuất hiện đầu (thường là chữ thường trong list Unicode).
    Đồng thời sắp theo độ dài giảm dần để ưu tiên chuỗi dài (quan trọng cho VNI).
    """
    mapping = {}
    for s, d in zip(src_list, dst_list):
        if s not in mapping:   # FIRST-WINS
            mapping[s] = d
    patterns = sorted(mapping.keys(), key=len, reverse=True)
    regex = re.compile("|".join(re.escape(p) for p in patterns))
    return regex, mapping

def _multi_replace(text, regex, mapping):
    if text is None:
        return None
    return regex.sub(lambda m: mapping[m.group(0)], text)

# Biên dịch regex/mapping (FIRST-WINS)
REG_TCVN3_to_Unicode, MAP_TCVN3_to_Unicode = _compile_regex_map_firstwins(_TCVN3, _Unicode)
REG_Unicode_to_TCVN3, MAP_Unicode_to_TCVN3 = _compile_regex_map_firstwins(_Unicode, _TCVN3)

REG_VNI_to_Unicode, MAP_VNI_to_Unicode   = _compile_regex_map_firstwins(_VNIWin, _Unicode)
REG_Unicode_to_VNI, MAP_Unicode_to_VNI   = _compile_regex_map_firstwins(_Unicode, _VNIWin)

REG_Unicode_to_KD, MAP_Unicode_to_KD     = _compile_regex_map_firstwins(_Unicode, _KhongDau)

# ====== Định dạng chữ (casing) ======
CASE_KEEP   = 0  # Giữ nguyên
CASE_UPPER  = 1  # HOA toàn bộ
CASE_LOWER  = 2  # thường toàn bộ
CASE_SENT   = 3  # Hoa đầu câu
CASE_TITLE  = 4  # Hoa Mỗi Từ

def _case_sentence(text: str) -> str:
    """
    Viết hoa chữ cái đầu câu cho Unicode (kể cả tiếng Việt).
    Quy tắc đơn giản: sau ., ?, !, …, xuống dòng → bắt đầu câu mới.
    Bảo toàn các ký tự khác (ngoặc, dấu nháy, khoảng trắng).
    """
    if not text:
        return text
    out = []
    sentence_start = True
    for ch in text:
        out.append(ch.upper() if sentence_start and ch.isalpha() else ch)
        # Kích hoạt bắt đầu câu mới sau các dấu kết thúc hoặc xuống dòng
        if ch in '.?!…\n\r':
            sentence_start = True
        elif ch.strip() != '':
            # gặp ký tự không phải khoảng trắng, không phải dấu kết thúc ⇒ đang trong câu
            sentence_start = False
    return ''.join(out)

def _case_title(text: str) -> str:
    """
    Viết hoa Mỗi Từ: đơn giản hóa — viết hoa ký tự chữ đầu mỗi segment phân cách bởi khoảng trắng.
    Đồng thời xử lý ký tự nối '-' và '_' bên trong từ.
    """
    if not text:
        return text
    def cap_word(w):
        if not w:
            return w
        chars = list(w)
        # viết hoa ký tự chữ đầu tiên trong segment
        for i, c in enumerate(chars):
            if c.isalpha():
                chars[i] = c.upper()
                break
        return ''.join(chars)

    parts = text.split(' ')
    for i, p in enumerate(parts):
        # tách tiếp theo '-' và '_' để viết hoa từng mảnh
        sub = re.split(r'([\-_/])', p)
        sub = [cap_word(x) if x not in '-_/' else x for x in sub]
        parts[i] = ''.join(sub)
    return ' '.join(parts)

def _apply_casing(text: str, mode: int) -> str:
    if text is None:
        return None
    if mode == CASE_KEEP:
        return text
    if mode == CASE_UPPER:
        return text.upper()
    if mode == CASE_LOWER:
        return text.lower()
    if mode == CASE_SENT:
        return _case_sentence(text)
    if mode == CASE_TITLE:
        return _case_title(text)
    return text

class VNEncodingConvertAlgorithm(QgsProcessingAlgorithm):
    """
    Chuyển đổi giá trị thuộc tính chuỗi giữa:
      - TCVN3 → Unicode
      - Unicode → TCVN3
      - VNIWin → Unicode
      - Unicode → VNIWin
    Tuỳ chọn:
      • 'Bỏ dấu (KhongDau)' áp dụng CHO TẤT CẢ chế độ: chuẩn hoá về Unicode → bỏ dấu → (áp dụng định dạng chữ) → ASCII
      • Định dạng chữ: Giữ nguyên / HOA / thường / Hoa đầu câu / Hoa Mỗi Từ
    """

    PARAM_INPUT     = 'INPUT'
    PARAM_FIELDS    = 'FIELDS'
    PARAM_MODE      = 'MODE'
    PARAM_KHONGDAU  = 'KHONGDAU'
    PARAM_CASEMODE  = 'CASEMODE'
    PARAM_OUTPUT    = 'OUTPUT'

    MODES = [
        'TCVN3 → Unicode',
        'Unicode → TCVN3',
        'VNIWin → Unicode',
        'Unicode → VNIWin'
    ]

    CASE_OPTIONS = [
        'Giữ nguyên',
        'VIẾT HOA TOÀN BỘ',
        'viết thường toàn bộ',
        'Viết hoa đầu câu',
        'Viết Hoa Mỗi Từ'
    ]

    def tr(self, text):
        return QCoreApplication.translate('VNEncodingConvert', text)

    def createInstance(self):
        return VNEncodingConvertAlgorithm()

    def name(self):
        return 'vn_encoding_convert'

    def displayName(self):
        return self.tr('Chuyển đổi bảng mã tiếng Việt')

    def group(self):
        return self.tr('Tiện ích tiếng Việt')

    def groupId(self):
        return 'vn_text_utils'

    def shortHelpString(self):
        return self.tr(
            "Chuyển đổi giá trị thuộc tính chuỗi giữa các bảng mã: "
            "TCVN3 ⇄ Unicode, VNIWin ⇄ Unicode.\n"
            "- Không thay đổi schema (tên/kiểu trường giữ nguyên).\n"
            "- Nếu không chọn trường, áp dụng cho tất cả trường kiểu string.\n"
            "- 'Bỏ dấu (KhongDau)' áp dụng cho TẤT CẢ chế độ: chuẩn hoá về Unicode rồi bỏ dấu, "
            "áp dụng định dạng chữ và TRẢ VỀ ASCII không dấu.\n"
            "- Tùy chọn định dạng chữ: Giữ nguyên / HOA / thường / Hoa đầu câu / Hoa Mỗi Từ."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.PARAM_INPUT, self.tr('Lớp vào'),
                [QgsProcessing.TypeVector]
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.PARAM_FIELDS,
                self.tr('Các trường chuỗi cần chuyển (để trống = tất cả trường chuỗi)'),
                parentLayerParameterName=self.PARAM_INPUT,
                type=QgsProcessingParameterField.String,
                allowMultiple=True,
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_MODE, self.tr('Chế độ chuyển đổi'),
                options=self.MODES, defaultValue=0
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAM_KHONGDAU,
                self.tr('Bỏ dấu (KhongDau) cho TẤT CẢ chế độ (đầu ra ASCII không dấu)'),
                defaultValue=False
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_CASEMODE,
                self.tr('Định dạng chữ'),
                options=self.CASE_OPTIONS,
                defaultValue=CASE_KEEP
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.PARAM_OUTPUT, self.tr('Lớp đã chuyển đổi')
            )
        )

    # --- Helpers cho nhánh "Bỏ dấu" ---
    def _to_unicode_from_mode(self, text, mode_idx):
        """
        Trả về phiên bản Unicode của `text` dựa trên chế độ hiện chọn,
        để nhánh 'Bỏ dấu' áp dụng nhất quán cho mọi nguồn.
        """
        if text is None:
            return None
        if mode_idx == 0:   # TCVN3 → Unicode
            return _multi_replace(text, REG_TCVN3_to_Unicode, MAP_TCVN3_to_Unicode)
        elif mode_idx == 1: # Unicode → TCVN3 (nguồn đã là Unicode)
            return text
        elif mode_idx == 2: # VNIWin → Unicode
            return _multi_replace(text, REG_VNI_to_Unicode, MAP_VNI_to_Unicode)
        elif mode_idx == 3: # Unicode → VNIWin (nguồn đã là Unicode)
            return text
        return text

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.PARAM_INPUT, context)
        if source is None:
            raise QgsProcessingException(self.tr('Không đọc được lớp đầu vào.'))

        selected_fields = self.parameterAsFields(parameters, self.PARAM_FIELDS, context)
        mode_idx = self.parameterAsEnum(parameters, self.PARAM_MODE, context)
        khong_dau = self.parameterAsBool(parameters, self.PARAM_KHONGDAU, context)
        case_mode = self.parameterAsEnum(parameters, self.PARAM_CASEMODE, context)

        # Lọc đúng các trường chuỗi theo QVariant.String
        string_field_names = [f.name() for f in source.fields() if f.type() == QVariant.String]
        if selected_fields:
            target_fields = [f for f in selected_fields if f in string_field_names]
        else:
            target_fields = string_field_names

        if not target_fields:
            feedback.pushInfo(self.tr('Không có trường chuỗi để xử lý. Sẽ sao chép lớp gốc.'))

        # Tạo sink giữ nguyên schema/geometry/CRS
        fields = QgsFields()
        for f in source.fields():
            fields.append(f)

        (sink, sink_id) = self.parameterAsSink(
            parameters, self.PARAM_OUTPUT, context,
            fields, source.wkbType(), source.sourceCrs()
        )

        # --- Convert theo chế độ, với override "Bỏ dấu" áp dụng mọi chế độ, rồi áp casing ---
        def convert_text(text):
            if text is None:
                return None

            if khong_dau:
                uni = self._to_unicode_from_mode(text, mode_idx)
                no_diac = _multi_replace(uni, REG_Unicode_to_KD, MAP_Unicode_to_KD)
                return _apply_casing(no_diac, case_mode)

            # Ngược lại: chuyển mã trước, sau đó áp dụng casing
            if mode_idx == 0:   # TCVN3 -> Unicode
                s = _multi_replace(text, REG_TCVN3_to_Unicode, MAP_TCVN3_to_Unicode)
            elif mode_idx == 1: # Unicode -> TCVN3
                s = _multi_replace(text, REG_Unicode_to_TCVN3, MAP_Unicode_to_TCVN3)
            elif mode_idx == 2: # VNIWin -> Unicode
                s = _multi_replace(text, REG_VNI_to_Unicode, MAP_VNI_to_Unicode)
            elif mode_idx == 3: # Unicode -> VNIWin
                s = _multi_replace(text, REG_Unicode_to_VNI, MAP_Unicode_to_VNI)
            else:
                s = text

            return _apply_casing(s, case_mode)

        total = source.featureCount()
        for i, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break

            new_feat = QgsFeature(fields)
            new_feat.setGeometry(feat.geometry())
            attrs = list(feat.attributes())

            if target_fields:
                for idx, fld in enumerate(fields):
                    name = fld.name()
                    if name in target_fields:
                        val = attrs[idx]
                        if isinstance(val, str):
                            try:
                                attrs[idx] = convert_text(val)
                            except Exception:
                                attrs[idx] = val  # an toàn: giữ nguyên nếu lỗi cục bộ

            new_feat.setAttributes(attrs)
            sink.addFeature(new_feat)

            if total:
                feedback.setProgress(int(100 * (i + 1) / total))

        return {self.PARAM_OUTPUT: sink_id}
