# -*- coding: utf-8 -*-
"""
QGIS 3.16+ Processing Algorithm (for Provider)
BỎ DẤU (ASCII) cho 1..n trường chuỗi từ BẤT KỲ bảng mã (Auto/Unicode/TCVN3/VNIWin)
Tùy chọn:
  • Định dạng chữ: Giữ nguyên / HOA / thường / Hoa đầu câu / Hoa Mỗi Từ
  • Xử lý khoảng trắng: Giữ nguyên / Xóa toàn bộ / Thay bằng '_' (gạch dưới)
  • Ghi đè trường gốc (in-place field) hoặc tạo trường mới hậu tố 'vt'
  • Cập nhật vào lớp gốc (in-place layer) hoặc tạo lớp/lưu mới
"""

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer, QgsProcessingParameterField,
    QgsProcessingParameterEnum, QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink, QgsProcessingException,
    QgsVectorLayer, QgsFields, QgsField, QgsFeature
)
import re

# ===== Bảng mã =====
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
_KhongDau = [
u'a',u'A',u'a',u'A',u'd',u'D',u'e',u'E',u'o',u'O',u'o',u'O',u'u',u'U',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',
u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',u'a',u'A',
u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'e',u'E',u'i',u'I',u'i',u'I',u'i',u'I',u'i',u'I',u'i',u'I',
u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',u'o',u'O',
u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'u',u'U',u'y',u'Y',u'y',u'Y',u'y',u'Y',u'y',u'Y',u'y',u'Y'
]

# ===== Regex mapping: FIRST-WINS + ưu tiên chuỗi dài =====
def _compile_regex_map_firstwins(src_list, dst_list):
    mapping = {}
    for s, d in zip(src_list, dst_list):
        if s not in mapping:
            mapping[s] = d
    patterns = sorted(mapping.keys(), key=len, reverse=True)
    regex = re.compile("|".join(re.escape(p) for p in patterns))
    return regex, mapping

def _multi_replace(text, regex, mapping):
    if text is None:
        return None
    return regex.sub(lambda m: mapping[m.group(0)], text)

REG_TCVN3_to_Unicode, MAP_TCVN3_to_Unicode = _compile_regex_map_firstwins(_TCVN3, _Unicode)
REG_VNI_to_Unicode,  MAP_VNI_to_Unicode   = _compile_regex_map_firstwins(_VNIWin, _Unicode)
REG_Unicode_to_KD,   MAP_Unicode_to_KD    = _compile_regex_map_firstwins(_Unicode, _KhongDau)

# ===== Heuristics phát hiện bảng mã =====
REG_DET_TCVN3 = re.compile("|".join(re.escape(c) for c in set(_TCVN3)))
REG_DET_VNI   = re.compile("|".join(re.escape(c) for c in sorted(set(_VNIWin), key=len, reverse=True)))

ENC_AUTO, ENC_UNI, ENC_TCVN3, ENC_VNI = range(4)

def detect_encoding(s: str) -> int:
    if not s:
        return ENC_UNI
    cnt_tcvn = len(REG_DET_TCVN3.findall(s))
    cnt_vni  = len(REG_DET_VNI.findall(s))
    if cnt_tcvn==0 and cnt_vni==0:
        return ENC_UNI
    return ENC_VNI if cnt_vni >= cnt_tcvn else ENC_TCVN3

def to_unicode(text: str, enc_mode: int) -> str:
    if text is None:
        return None
    if enc_mode == ENC_UNI:
        return text
    if enc_mode == ENC_TCVN3:
        return _multi_replace(text, REG_TCVN3_to_Unicode, MAP_TCVN3_to_Unicode)
    if enc_mode == ENC_VNI:
        return _multi_replace(text, REG_VNI_to_Unicode, MAP_VNI_to_Unicode)
    det = detect_encoding(text)  # AUTO
    if det == ENC_TCVN3:
        return _multi_replace(text, REG_TCVN3_to_Unicode, MAP_TCVN3_to_Unicode)
    if det == ENC_VNI:
        return _multi_replace(text, REG_VNI_to_Unicode, MAP_VNI_to_Unicode)
    return text

# ===== Định dạng chữ =====
CASE_KEEP, CASE_UPPER, CASE_LOWER, CASE_SENT, CASE_TITLE = range(5)

def _case_sentence(text: str) -> str:
    if not text:
        return text
    out, sentence_start = [], True
    for ch in text:
        out.append(ch.upper() if sentence_start and ch.isalpha() else ch)
        if ch in '.?!…\n\r':
            sentence_start = True
        elif ch.strip() != '':
            sentence_start = False
    return ''.join(out)

def _case_title(text: str) -> str:
    if not text:
        return text
    def cap_word(w):
        if not w: return w
        chars = list(w)
        for i, c in enumerate(chars):
            if c.isalpha():
                chars[i] = c.upper()
                break
        return ''.join(chars)
    parts = text.split(' ')
    for i, p in enumerate(parts):
        sub = re.split(r'([\-_/])', p)
        sub = [cap_word(x) if x not in '-_/' else x for x in sub]
        parts[i] = ''.join(sub)
    return ' '.join(parts)

def apply_casing(text: str, mode: int) -> str:
    if text is None:
        return None
    if mode == CASE_KEEP:  return text
    if mode == CASE_UPPER: return text.upper()
    if mode == CASE_LOWER: return text.lower()
    if mode == CASE_SENT:  return _case_sentence(text)
    if mode == CASE_TITLE: return _case_title(text)
    return text

# ===== Xử lý khoảng trắng =====
SPACE_KEEP, SPACE_REMOVE, SPACE_UNDERSCORE = range(3)

def transform_spaces(s: str, mode: int) -> str:
    if not s:
        return s
    if mode == SPACE_KEEP:
        return s
    if mode == SPACE_REMOVE:
        # xóa TẤT CẢ khoảng trắng (space, tab, newline)
        return re.sub(r'\s+', '', s)
    if mode == SPACE_UNDERSCORE:
        # thay mọi cụm khoảng trắng bằng 1 dấu '_', đồng thời bỏ '_' thừa ở đầu/cuối
        s2 = re.sub(r'\s+', '_', s)
        return s2.strip('_')
    return s

class VNStripDiacriticsAlgorithm(QgsProcessingAlgorithm):
    """
    Bỏ DẤU (ASCII) cho một/multiple trường chuỗi từ bảng mã BẤT KỲ.
    Tùy chọn: định dạng chữ; xử lý khoảng trắng; ghi in-place field/new field 'vt';
              in-place layer/new layer.
    """

    PARAM_INPUT      = 'INPUT'
    PARAM_FIELDS     = 'FIELDS'
    PARAM_ENCODING   = 'ENCODING'        # Auto / Unicode / TCVN3 / VNIWin
    PARAM_CASEMODE   = 'CASEMODE'        # casing
    PARAM_SPACE_MODE = 'SPACE_MODE'      # keep/remove/underscore
    PARAM_FIELD_MODE = 'FIELD_MODE'      # in-place field / new field 'vt'
    PARAM_LAYER_MODE = 'LAYER_MODE'      # update layer / new layer
    PARAM_OUTPUT     = 'OUTPUT'

    FIELD_INPLACE, FIELD_NEW = range(2)
    LAYER_UPDATE, LAYER_NEW  = range(2)

    ENCODING_OPTS   = ['Auto detect', 'Unicode', 'TCVN3', 'VNIWin']
    CASE_OPTS       = ['Giữ nguyên', 'VIẾT HOA TOÀN BỘ', 'viết thường toàn bộ', 'Viết hoa đầu câu', 'Viết Hoa Mỗi Từ']
    SPACE_OPTS      = ['Giữ nguyên', 'Xóa toàn bộ', 'Thay bằng "_"']
    FIELD_MODE_OPTS = ['Cập nhật vào trường gốc (in-place field)', 'Tạo trường mới với hậu tố "vt"']
    LAYER_MODE_OPTS = ['Cập nhật vào lớp gốc (in-place layer)', 'Tạo lớp/lưu mới']

    # ====== Metadata hiển thị ======
    def tr(self, text):
        return QCoreApplication.translate('VNStripDiacritics', text)

    def createInstance(self):
        return VNStripDiacriticsAlgorithm()

    def name(self):
        return 'vn_strip_diacritics'

    def displayName(self):
        return self.tr('Bỏ dấu ký tự tiếng Việt')

    def group(self):
        return self.tr('Tiện tích tiếng Việt')

    def groupId(self):
        return 'vn_text_utils'

    def shortHelpString(self):
        return self.tr(
            "Chuyển giá trị thuộc tính sang KHÔNG DẤU (ASCII) từ bất kỳ bảng mã (Auto/Unicode/TCVN3/VNIWin).\n"
            "- Không đổi bảng mã; chỉ chuẩn hoá về Unicode nội bộ rồi bỏ dấu.\n"
            "- Tùy chọn: Định dạng chữ (Giữ/HOA/thường/Hoa đầu câu/Hoa Mỗi Từ), "
            "xử lý khoảng trắng (Giữ/Xóa/Thay bằng '_'), ghi đè trường gốc hoặc tạo trường mới 'vt', "
            "cập nhật lớp gốc hoặc tạo lớp/lưu mới.\n"
            "- Nếu không chọn trường, áp dụng cho tất cả trường kiểu string."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.PARAM_INPUT, self.tr('Lớp vào'),
                [QgsProcessing.TypeVector]
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.PARAM_FIELDS,
                self.tr('Các trường chuỗi cần xử lý (để trống = tất cả trường chuỗi)'),
                parentLayerParameterName=self.PARAM_INPUT,
                type=QgsProcessingParameterField.String,
                allowMultiple=True,
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_ENCODING,
                self.tr('Chế độ nhận diện mã nguồn'),
                options=self.ENCODING_OPTS,
                defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_CASEMODE,
                self.tr('Định dạng chữ'),
                options=self.CASE_OPTS,
                defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_SPACE_MODE,
                self.tr('Xử lý khoảng trắng'),
                options=self.SPACE_OPTS,
                defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_FIELD_MODE,
                self.tr('Cách ghi trường kết quả'),
                options=self.FIELD_MODE_OPTS,
                defaultValue=self.FIELD_INPLACE
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_LAYER_MODE,
                self.tr('Cách ghi lớp đầu ra'),
                options=self.LAYER_MODE_OPTS,
                defaultValue=self.LAYER_UPDATE
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.PARAM_OUTPUT,
                self.tr('Lớp kết quả (nếu tạo lớp mới)')
            )
        )

    # Tên trường mới duy nhất với hậu tố 'vt'
    def _unique_field_name(self, fields: QgsFields, base: str) -> str:
        candidate = f"{base}vt"
        if fields.indexFromName(candidate) < 0:
            return candidate
        i = 1
        while fields.indexFromName(f"{candidate}{i}") >= 0:
            i += 1
        return f"{candidate}{i}"

    def processAlgorithm(self, parameters, context, feedback):
        vlayer = self.parameterAsVectorLayer(parameters, self.PARAM_INPUT, context)
        if vlayer is None:
            raise QgsProcessingException(self.tr('Không đọc được lớp đầu vào.'))

        selected_fields = self.parameterAsFields(parameters, self.PARAM_FIELDS, context)
        enc_idx      = self.parameterAsEnum(parameters, self.PARAM_ENCODING, context)
        case_mode    = self.parameterAsEnum(parameters, self.PARAM_CASEMODE, context)
        space_mode   = self.parameterAsEnum(parameters, self.PARAM_SPACE_MODE, context)
        field_mode   = self.parameterAsEnum(parameters, self.PARAM_FIELD_MODE, context)
        layer_mode   = self.parameterAsEnum(parameters, self.PARAM_LAYER_MODE, context)

        # Lọc các trường chuỗi
        string_field_names = [f.name() for f in vlayer.fields() if f.type() == QVariant.String]
        target_fields = [n for n in (selected_fields or string_field_names) if n in string_field_names]
        if not target_fields:
            raise QgsProcessingException(self.tr('Không có trường chuỗi để xử lý.'))

        # Pipeline: Any-encoding -> Unicode -> KHÔNG DẤU (ASCII) -> Casing -> Space transform
        enc_map = [ENC_AUTO, ENC_UNI, ENC_TCVN3, ENC_VNI]
        def to_ascii_final(text: str) -> str:
            uni = to_unicode(text, enc_map[enc_idx])
            no_diac = _multi_replace(uni, REG_Unicode_to_KD, MAP_Unicode_to_KD)
            no_diac = apply_casing(no_diac, case_mode)
            no_diac = transform_spaces(no_diac, space_mode)
            return no_diac

        if layer_mode == self.LAYER_UPDATE:
            # In-place layer
            if field_mode == self.FIELD_NEW:
                vlayer.startEditing()
                new_fields_added = {}
                for fname in target_fields:
                    base_f = vlayer.fields()[vlayer.fields().indexFromName(fname)]
                    new_name = self._unique_field_name(vlayer.fields(), fname)
                    new_field = QgsField(new_name, QVariant.String, '', base_f.length(), base_f.precision())
                    if not vlayer.addAttribute(new_field):
                        vlayer.rollBack()
                        raise QgsProcessingException(self.tr(f'Không thể thêm trường mới: {new_name}'))
                    new_fields_added[fname] = new_name
                vlayer.updateFields()

                for i, feat in enumerate(vlayer.getFeatures()):
                    updates = {}
                    for src_name, dst_name in new_fields_added.items():
                        val = feat[src_name]
                        if isinstance(val, str):
                            updates[vlayer.fields().indexFromName(dst_name)] = to_ascii_final(val)
                    if updates:
                        vlayer.changeAttributeValues(feat.id(), updates)
                    if (i+1) % 1000 == 0:
                        feedback.pushInfo(self.tr(f'Đã xử lý {i+1} đối tượng...'))

                if not vlayer.commitChanges():
                    raise QgsProcessingException(self.tr('Không thể commit thay đổi vào lớp gốc.'))
                return {self.PARAM_OUTPUT: vlayer.id()}

            else:
                # FIELD_INPLACE
                vlayer.startEditing()
                idx_map = [vlayer.fields().indexFromName(n) for n in target_fields]
                for i, feat in enumerate(vlayer.getFeatures()):
                    updates = {}
                    for idx, name in zip(idx_map, target_fields):
                        val = feat[name]
                        if isinstance(val, str):
                            updates[idx] = to_ascii_final(val)
                    if updates:
                        vlayer.changeAttributeValues(feat.id(), updates)
                    if (i+1) % 1000 == 0:
                        feedback.pushInfo(self.tr(f'Đã xử lý {i+1} đối tượng...'))
                if not vlayer.commitChanges():
                    raise QgsProcessingException(self.tr('Không thể commit thay đổi vào lớp gốc.'))
                return {self.PARAM_OUTPUT: vlayer.id()}

        else:
            # NEW LAYER
            fields = QgsFields()
            for f in vlayer.fields():
                fields.append(f)
            vt_name_map = {}
            if field_mode == self.FIELD_NEW:
                for fname in target_fields:
                    base_f = vlayer.fields()[vlayer.fields().indexFromName(fname)]
                    new_name = self._unique_field_name(fields, fname)
                    fields.append(QgsField(new_name, QVariant.String, '', base_f.length(), base_f.precision()))
                    vt_name_map[fname] = new_name

            (sink, sink_id) = self.parameterAsSink(
                parameters, self.PARAM_OUTPUT, context,
                fields, vlayer.wkbType(), vlayer.sourceCrs()
            )
            if sink is None:
                raise QgsProcessingException(self.tr('Không tạo được layer đầu ra.'))

            for i, feat in enumerate(vlayer.getFeatures()):
                new_feat = QgsFeature(fields)
                new_feat.setGeometry(feat.geometry())
                attrs = list(feat.attributes())

                if field_mode == self.FIELD_NEW:
                    while len(attrs) < fields.count():
                        attrs.append(None)
                    for src_name, dst_name in vt_name_map.items():
                        val = feat[src_name]
                        if isinstance(val, str):
                            attrs[fields.indexFromName(dst_name)] = to_ascii_final(val)
                else:
                    for name in target_fields:
                        val = feat[name]
                        if isinstance(val, str):
                            attrs[fields.indexFromName(name)] = to_ascii_final(val)

                new_feat.setAttributes(attrs)
                sink.addFeature(new_feat)

                if (i+1) % 1000 == 0:
                    feedback.pushInfo(self.tr(f'Đã xử lý {i+1} đối tượng...'))

            return {self.PARAM_OUTPUT: sink_id}
