# -*- coding: utf-8 -*-
"""
Export to MicroStation DGN (v7) with optional multi-line labeling (one field per line).
- Không CreateField (DGN v7 thường không hỗ trợ); chỉ SetField nếu field đã tồn tại.
- Ép 2D ở phía OGR: FlattenTo2D().
- Nhãn: chọn NHIỀU TRƯỜNG → tự động ghép BẰNG XUỐNG DÒNG, tự động loại bỏ MỌI ký tự nháy (' và "),
  tránh đè nhãn cơ bản (dịch xoắn ốc) và đảm bảo NHÃN NẰM TRONG POLYGON (pointOnSurface).
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsProcessingParameterNumber,
    QgsProcessingParameterCrs,
    QgsProcessingParameterBoolean,
    QgsProcessingFeedback,
    QgsProcessingException,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsWkbTypes,
    QgsGeometry,
    QgsPointXY,
)
from osgeo import ogr, osr
import math


class ExportToDGNWithLabelsAlgorithm(QgsProcessingAlgorithm):
    # IO / CRS
    INPUT = 'INPUT'
    OUTPUT = 'OUTPUT'
    CRS = 'CRS'
    REPROJECT = 'REPROJECT'

    # Geometry styling
    LEVEL_MODE = 'LEVEL_MODE'
    LEVEL_FIELD = 'LEVEL_FIELD'
    LEVEL_DEFAULT = 'LEVEL_DEFAULT'
    COLOR_INDEX = 'COLOR_INDEX'
    LINE_WEIGHT = 'LINE_WEIGHT'

    # Labeling
    LABEL_CREATE = 'LABEL_CREATE'
    LABEL_FIELDS_MULTI = 'LABEL_FIELDS_MULTI'
    LABEL_FONT = 'LABEL_FONT'
    LABEL_HEIGHT = 'LABEL_HEIGHT'
    LABEL_LEVEL = 'LABEL_LEVEL'
    LABEL_COLOR_INDEX = 'LABEL_COLOR_INDEX'
    USE_CENTROID_FOR_POLY = 'USE_CENTROID_FOR_POLY'  # giữ tương thích, nhưng thực tế luôn dùng pointOnSurface()

    # De-conflict labels
    LABEL_AVOID_OVERLAP = 'LABEL_AVOID_OVERLAP'
    LABEL_MIN_DIST = 'LABEL_MIN_DIST'
    LABEL_OFFSET_STEP = 'LABEL_OFFSET_STEP'
    LABEL_MAX_ITERS = 'LABEL_MAX_ITERS'

    # ------------- UI -------------
    def initAlgorithm(self, config=None):
        # Input
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr("Lớp đầu vào (SHP/TAB/… OGR)"),
                [QgsProcessing.TypeVectorAnyGeometry]
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT,
                self.tr("Tệp DGN đầu ra"),
                self.tr("MicroStation DGN (*.dgn)")
            )
        )

        # CRS
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.REPROJECT,
                self.tr("Chuyển hệ toạ độ đầu vào sang CRS đích trước khi ghi"),
                defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.CRS,
                self.tr("CRS đích (nếu bật chuyển hệ)"),
                defaultValue=QgsCoordinateReferenceSystem('EPSG:4326')
            )
        )

        # Geometry styling
        self.addParameter(
            QgsProcessingParameterEnum(
                self.LEVEL_MODE,
                self.tr("Cách gán DGN Level cho đối tượng"),
                options=[self.tr("Theo tên lớp đầu vào"), self.tr("Theo trường thuộc tính")],
                defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.LEVEL_FIELD,
                self.tr("Trường dùng làm Level (0–63)"),
                parentLayerParameterName=self.INPUT,
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LEVEL_DEFAULT,
                self.tr("Level mặc định (0–63) nếu không xác định được"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=0, minValue=0, maxValue=63
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.COLOR_INDEX,
                self.tr("Màu cho đối tượng (0–255, -1 giữ mặc định)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=-1, minValue=-1, maxValue=255
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LINE_WEIGHT,
                self.tr("Line Weight (0–31)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=0, minValue=0, maxValue=31
            )
        )

        # Labeling
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.LABEL_CREATE,
                self.tr("Tạo nhãn (text element)"),
                defaultValue=False
            )
        )
        p = QgsProcessingParameterField(
            self.LABEL_FIELDS_MULTI,
            self.tr("Trường nhãn (có thể chọn nhiều) — mỗi trường sẽ xuống dòng"),
            parentLayerParameterName=self.INPUT,
            optional=True
        )
        p.setAllowMultiple(True)
        self.addParameter(p)

        self.addParameter(
            QgsProcessingParameterString(
                self.LABEL_FONT,
                self.tr("Font chữ nhãn"),
                defaultValue="Arial"
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LABEL_HEIGHT,
                self.tr("Chiều cao chữ (đơn vị bản đồ)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=2.5, minValue=0.1
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LABEL_LEVEL,
                self.tr("Level cho nhãn (0–63)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=10, minValue=0, maxValue=63
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LABEL_COLOR_INDEX,
                self.tr("Màu cho nhãn (0–255, -1 giữ mặc định)"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=-1, minValue=-1, maxValue=255
            )
        )
        # giữ để tương thích UI cũ, nhưng luôn dùng pointOnSurface()
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.USE_CENTROID_FOR_POLY,
                self.tr("Polygon sẽ luôn đặt nhãn bằng pointOnSurface()"),
                defaultValue=False
            )
        )

        # De-conflict labels
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.LABEL_AVOID_OVERLAP,
                self.tr("Hạn chế nhãn đè lên nhau (dịch chuyển tự động)"),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LABEL_MIN_DIST,
                self.tr("Khoảng cách tối thiểu giữa các nhãn (đơn vị bản đồ)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=5.0, minValue=0.0
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LABEL_OFFSET_STEP,
                self.tr("Bước dịch khi tránh đè (đơn vị bản đồ)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=2.0, minValue=0.1
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.LABEL_MAX_ITERS,
                self.tr("Số vòng thử dịch tối đa mỗi nhãn"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=24, minValue=1, maxValue=200
            )
        )

    # ------------- Meta -------------
    def name(self):
        return "export_to_dgn_with_labels"

    def displayName(self):
        return self.tr("Chuyển lớp Vector sang DGN")

    def group(self):
        return self.tr("Tiện ích Vector")  # theo yêu cầu

    def groupId(self):
        return "vector_utils"              # theo yêu cầu

    def shortHelpString(self):
        return self.tr("""
    Chuyển lớp Vector sang DGN (MicroStation V7)**  

    Thuật toán cho phép xuất lớp vector (SHP/TAB/OGR) sang định dạng DGN v7,  
    giữ hình học 2D, gán Level/Color/Weight và sinh nhãn (Text).  

    Tham số đầu vào:
    - Lớp đầu vào: lớp vector nguồn (SHP, TAB, GeoPackage, …).  
    - Tệp DGN đầu ra: đường dẫn tệp .dgn sẽ tạo.  
    - Chuyển hệ toạ độ: nếu chọn, dữ liệu sẽ được chuyển sang CRS đích trước khi ghi.  
    - CRS đích (CRS): hệ toạ độ mục tiêu (mặc định EPSG:4326).  

    Tham số đối tượng:
    - Cách gán Level: 0 = theo tên lớp, 1 = theo giá trị trường thuộc tính.  
    - Trường dùng làm Level: tên trường chứa Level (0–63).  
    - Level mặc định: giá trị mặc định (0–63) nếu không xác định được từ trường.  
    - Màu đối tượng: mã màu (0–255, -1 giữ mặc định).  
    - Lực nét: độ dày nét (0–31).  

    Tham số nhãn:
    - Tạo nhãn: bật/tắt xuất nhãn.  
    - Trường nhãn: có thể chọn nhiều trường → mỗi trường = một dòng text.  
    - Font chữ: tên font (ví dụ Arial, Tahoma, …).  
    - Chiều cao chữ: kích thước chữ (đơn vị bản đồ).  
    - Level cho nhãn: Level DGN (0–63) cho nhãn.  
    - ColorIndex cho nhãn: mã màu (0–255, -1 giữ mặc định).  
    - Polygon đặt nhãn: Thuật toán luôn đặt nhãn bằng nằm trong polygon.  

    Tham số tránh chồng lấn nhãn:
    - Hạn chế đè: bật để dịch chuyển nhãn khi gần nhau.  
    - Khoảng cách tối thiểu: khoảng cách giữa các nhãn (đơn vị bản đồ).  
    - Bước dịch: bước dịch khi thử tránh chồng.  
    - Số vòng thử: số vòng dịch tối đa cho mỗi nhãn.  

    Ghi chú:
    - Xuất nhãn bằng nhiều text element, mỗi dòng một element.  
    - DGN v7 chỉ hỗ trợ hình học 2D, các giá trị Z/M bị bỏ.  
    - Bảng thuộc tính trong DGN rất hạn chế, chỉ lưu các field hệ thống (Level, Color, Weight, Text…).  
    """)


    def tr(self, s):
        return QCoreApplication.translate('Processing', s)

    def createInstance(self):
        return ExportToDGNWithLabelsAlgorithm()

    # -------- Helpers --------
    def _qcrs_to_osr(self, qcrs: QgsCoordinateReferenceSystem) -> osr.SpatialReference:
        srs = osr.SpatialReference()
        authid = qcrs.authid()
        if authid and authid.upper().startswith("EPSG:"):
            try:
                srs.ImportFromEPSG(int(authid.split(":")[1]))
                return srs
            except Exception:
                pass
        srs.ImportFromWkt(qcrs.toWkt())
        return srs

    def _line_midpoint(self, geom: QgsGeometry) -> QgsGeometry:
        length = geom.length()
        if length <= 0:
            return geom.pointOnSurface()
        return geom.interpolate(length / 2.0)

    def _safe_layer_name(self, src):
        try:
            if hasattr(src, "sourceName") and callable(getattr(src, "sourceName")):
                n = src.sourceName()
                if n:
                    return n
        except Exception:
            pass
        try:
            if hasattr(src, "name") and callable(getattr(src, "name")):
                n2 = src.name()
                if n2:
                    return n2
        except Exception:
            pass
        return "layer"

    @staticmethod
    def _strip_quotes_auto(text: str) -> str:
        """Gỡ nháy bọc đầu-cuối rồi XOÁ toàn bộ ký tự nháy còn lại."""
        if text is None:
            return ""
        s = str(text).strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            s = s[1:-1].strip()
        # xoá mọi nháy đơn/đôi còn lại
        s = s.replace("'", "").replace('"', "")
        return s

    @staticmethod
    def _spiral_offsets(step: float, iters: int):
        angles = [0, 90, 180, 270, 45, 135, 225, 315]  # độ
        k = 0
        radius = step
        for _ in range(iters):
            a = math.radians(angles[k % len(angles)])
            yield (radius * math.cos(a), radius * math.sin(a))
            k += 1
            if k % len(angles) == 0:
                radius += step

    @staticmethod
    def _dist2(p1: QgsPointXY, p2: QgsPointXY) -> float:
        dx = p1.x() - p2.x()
        dy = p1.y() - p2.y()
        return dx*dx + dy*dy

    # -------- Main --------
    def processAlgorithm(self, parameters, context, feedback: QgsProcessingFeedback):
        src = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None:
            raise QgsProcessingException(self.tr("Không lấy được nguồn dữ liệu đầu vào."))

        out_path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        reproject = self.parameterAsBool(parameters, self.REPROJECT, context)
        dest_crs = self.parameterAsCrs(parameters, self.CRS, context)

        level_mode = self.parameterAsEnum(parameters, self.LEVEL_MODE, context)
        level_field = self.parameterAsFields(parameters, self.LEVEL_FIELD, context)
        level_default = self.parameterAsInt(parameters, self.LEVEL_DEFAULT, context)
        color_index = self.parameterAsInt(parameters, self.COLOR_INDEX, context)
        line_weight = self.parameterAsInt(parameters, self.LINE_WEIGHT, context)

        # Label params
        make_labels = self.parameterAsBool(parameters, self.LABEL_CREATE, context)
        label_fields = self.parameterAsFields(parameters, self.LABEL_FIELDS_MULTI, context) or []
        label_font = self.parameterAsString(parameters, self.LABEL_FONT, context)
        label_height = self.parameterAsDouble(parameters, self.LABEL_HEIGHT, context)
        label_level = self.parameterAsInt(parameters, self.LABEL_LEVEL, context)
        label_color_index = self.parameterAsInt(parameters, self.LABEL_COLOR_INDEX, context)
        # USE_CENTROID_FOR_POLY vẫn nhận nhưng bị bỏ qua: luôn pointOnSurface

        # Overlap control
        avoid_overlap = self.parameterAsBool(parameters, self.LABEL_AVOID_OVERLAP, context)
        min_dist = max(0.0, self.parameterAsDouble(parameters, self.LABEL_MIN_DIST, context))
        step = max(0.1, self.parameterAsDouble(parameters, self.LABEL_OFFSET_STEP, context))
        max_iters = self.parameterAsInt(parameters, self.LABEL_MAX_ITERS, context)

        drv = ogr.GetDriverByName("DGN")
        if drv is None:
            raise QgsProcessingException(self.tr("Không tìm thấy driver GDAL 'DGN'."))

        try:
            drv.DeleteDataSource(out_path)
        except Exception:
            pass

        ds = drv.CreateDataSource(out_path)
        if ds is None:
            raise QgsProcessingException(self.tr("Không tạo được tệp DGN đầu ra."))

        target_crs = dest_crs if reproject else src.sourceCrs()
        srs = self._qcrs_to_osr(target_crs)

        dgn_lyr = ds.CreateLayer("elements", srs, geom_type=ogr.wkbUnknown)
        if dgn_lyr is None:
            raise QgsProcessingException(self.tr("Không tạo được layer DGN."))

        lyr_defn = dgn_lyr.GetLayerDefn()
        existing_fields = {lyr_defn.GetFieldDefn(i).GetNameRef(): i for i in range(lyr_defn.GetFieldCount())}

        def set_field_if_exists(ogr_feat, name, value):
            if name in existing_fields and value is not None:
                try:
                    ogr_feat.SetField(name, value)
                except Exception:
                    pass

        xform = None
        if reproject and src.sourceCrs().isValid() and target_crs.isValid() and src.sourceCrs() != target_crs:
            xform = QgsCoordinateTransform(src.sourceCrs(), target_crs, context.transformContext())

        base_lname = self._safe_layer_name(src)

        def clamp_level(v, default_v):
            try:
                iv = int(v)
                return max(0, min(63, iv))
            except Exception:
                return max(0, min(63, int(default_v)))

        total = src.featureCount() if src.featureCount() >= 0 else 0
        processed = 0

        placed = []  # vị trí điểm nhãn đã đặt
        min_dist2 = min_dist * min_dist

        for f in src.getFeatures():
            if feedback.isCanceled():
                break

            qgeom = f.geometry()
            if not qgeom or qgeom.isEmpty():
                continue

            if xform is not None:
                try:
                    qgeom = QgsGeometry(qgeom)
                    qgeom.transform(xform)
                except Exception:
                    continue

            # ---- Object geometry -> OGR 2D ----
            try:
                g_ogr = ogr.CreateGeometryFromWkb(bytes(qgeom.asWkb()))
            except Exception:
                g_ogr = None
            if g_ogr is None:
                continue
            try:
                g_ogr.FlattenTo2D()
            except Exception:
                pass

            feat = ogr.Feature(lyr_defn)
            feat.SetGeometry(g_ogr)

            # Level
            if level_mode == 0:
                lvl = abs(hash(base_lname)) % 64
            else:
                if level_field and level_field[0] in f.fields().names():
                    lvl = clamp_level(f[level_field[0]], level_default)
                else:
                    lvl = clamp_level(level_default, level_default)

            set_field_if_exists(feat, "Level", int(lvl))
            if color_index >= 0:
                set_field_if_exists(feat, "ColorIndex", int(color_index))
            set_field_if_exists(feat, "Weight", int(line_weight))

            try:
                dgn_lyr.CreateFeature(feat)
            except Exception:
                pass
            feat = None

            # ---------------- Labels ----------------
            if not make_labels or not label_fields:
                processed += 1
                if total > 0:
                    feedback.setProgress(int(processed * 100.0 / total))
                continue

            # Build multi-line label: 1 field / line, auto strip + remove quotes
            parts = []
            fields_names = f.fields().names()
            for fld in label_fields:
                if fld in fields_names:
                    val = f[fld]
                    if val is None:
                        continue
                    s = self._strip_quotes_auto(val).strip()
                    if s != "":
                        parts.append(s)
            if not parts:
                processed += 1
                if total > 0:
                    feedback.setProgress(int(processed * 100.0 / total))
                continue

            # GHÉP BẰNG XUỐNG DÒNG
            txt_str = "\n".join(parts)

            # Chọn điểm đặt nhãn:
            # - Polygon: LUÔN pointOnSurface() (đảm bảo nằm trong)
            # - Line: midpoint
            # - Khác: pointOnSurface
            gtype = QgsWkbTypes.geometryType(qgeom.wkbType())
            if gtype == QgsWkbTypes.PolygonGeometry:
                gpt = qgeom.pointOnSurface()
            elif gtype == QgsWkbTypes.LineGeometry:
                gpt = self._line_midpoint(qgeom)
            else:
                gpt = qgeom.pointOnSurface()

            if not gpt or gpt.isEmpty():
                processed += 1
                if total > 0:
                    feedback.setProgress(int(processed * 100.0 / total))
                continue

            # Lấy toạ độ điểm an toàn
            try:
                pt = gpt.asPoint()
            except Exception:
                try:
                    pt = gpt.centroid().asPoint()
                except Exception:
                    bbox = gpt.boundingBox()
                    pt = QgsPointXY((bbox.xMinimum()+bbox.xMaximum())/2.0,
                                    (bbox.yMinimum()+bbox.yMaximum())/2.0)

            anchor = QgsPointXY(pt.x(), pt.y())

            # Tránh đè cơ bản (khoảng cách giữa điểm neo nhãn)
            place_pt = QgsPointXY(anchor)
            if avoid_overlap and placed and min_dist > 0:
                def is_free(p: QgsPointXY):
                    for q in placed:
                        if self._dist2(p, q) < min_dist2:
                            return False
                    return True
                if not is_free(place_pt):
                    for dx, dy in self._spiral_offsets(step, max_iters):
                        cand = QgsPointXY(anchor.x() + dx, anchor.y() + dy)
                        if is_free(cand):
                            place_pt = cand
                            break

            # Ghi nhãn
            try:
                gpt_ogr = ogr.CreateGeometryFromWkb(bytes(QgsGeometry.fromPointXY(place_pt).asWkb()))
            except Exception:
                gpt_ogr = None
            if gpt_ogr is not None:
                try:
                    gpt_ogr.FlattenTo2D()
                except Exception:
                    pass

                ftxt = ogr.Feature(lyr_defn)
                ftxt.SetGeometry(gpt_ogr)
                set_field_if_exists(ftxt, "Level", int(label_level))
                if label_color_index >= 0:
                    set_field_if_exists(ftxt, "ColorIndex", int(label_color_index))
                set_field_if_exists(ftxt, "Weight", 0)

                # TextString (nếu field tồn tại). ĐÃ LOẠI BỎ MỌI NHÁY nên an toàn.
                set_field_if_exists(ftxt, "TextString", txt_str)

                # StyleString LABEL: t:'...' — vì đã xoá mọi nháy nên không cần escape,
                # vẫn thay thế nháy đơn đề phòng (không còn gì để thay, nhưng an toàn).
                safe_txt = txt_str.replace("'", "").replace('"', "")
                # Dòng mới: dùng \n trong chuỗi; MicroStation DGN v7 qua GDAL thường chấp nhận \n.
                style = "LABEL(f:'{font}',s:{size},t:'{txt}')".format(
                    font=label_font.replace("'", "''"),
                    size=float(label_height),
                    txt=safe_txt
                )
                try:
                    ftxt.SetStyleString(style)
                except Exception:
                    pass

                try:
                    dgn_lyr.CreateFeature(ftxt)
                    placed.append(place_pt)
                except Exception:
                    pass
                ftxt = None

            processed += 1
            if total > 0:
                feedback.setProgress(int(processed * 100.0 / total))

        dgn_lyr = None
        ds = None
        return {self.OUTPUT: out_path}
