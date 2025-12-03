# -*- coding: utf-8 -*-
# QGIS 3.16+ compatible
# File: Forestry_tool/algorithms/split_inplace_algorithm.py

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterVectorLayer, QgsProcessingParameterBoolean, QgsProcessingParameterField,
    QgsProcessingParameterEnum,
    QgsVectorLayer, QgsFeature, QgsField, QgsFields,
    QgsDistanceArea, QgsProject, QgsWkbTypes, QgsProcessingUtils, QgsUnitTypes
)

class SplitPolygonsInPlaceAlgorithm(QgsProcessingAlgorithm):
    # ----- IDs & Labels -----
    ALG_NAME = 'split_polygons_inplace_by_lines'
    ALG_DISPLAY = 'Chia tách polygon bằng đường (in-place)'
    GRP_NAME = 'Tiện ích Vector'
    GRP_ID = 'vector_utils'  # Nhóm hiển thị trong Toolbox (provider id vẫn do Provider của plugin quyết định)

    # ----- Parameter keys -----
    P_INPUT = 'INPUT'
    P_LINES = 'LINES'
    P_SELECTED_ONLY = 'SELECTED_ONLY'
    P_PRESERVE = 'PRESERVE'
    P_VALUE_FIELD = 'VALUE_FIELD'
    P_RECALC_AREA = 'RECALC_AREA'
    P_AREA_FIELD = 'AREA_FIELD'
    P_AREA_UNITS_MODE = 'AREA_UNITS_MODE'  # 0=Project default, 1=m2, 2=ha

    AREA_UNIT_OPTIONS = ['Theo Project', 'm²', 'hecta']

    # ===== REQUIRED: empty __init__ + createInstance() =====
    def __init__(self):
        super().__init__()

    def createInstance(self):
        return SplitPolygonsInPlaceAlgorithm()

    # ===== Meta =====
    def tr(self, text):
        return QCoreApplication.translate('SplitPolygonsInPlaceAlgorithm', text)

    def name(self):
        return self.ALG_NAME

    def displayName(self):
        return self.ALG_DISPLAY

    def group(self):
        return self.GRP_NAME

    def groupId(self):
        return self.GRP_ID

    def shortHelpString(self):
        return self.tr(
            "- INPUT: Lớp polygon sẽ bị ghi đè in-place.\n"
            "- LINES: Lớp đường cắt (line) — tự động ép CRS về INPUT nếu khác.\n"
            "- Chỉ đối tượng được chọn: chỉ tách các feature đang Select.\n"
            "- Bảo toàn dữ liệu: phân phối giá trị trường số theo tỉ lệ diện tích sau tách.\n"
            "- Tính lại diện tích: ghi diện tích mới vào trường đã chọn.\n"
            "- Đơn vị diện tích: Theo Project, m², hoặc hecta.\n"
            "Khuyến nghị dùng CRS đơn vị mét để diện tích chính xác."
        )

    # ===== Parameters =====
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.P_INPUT, self.tr('Lớp polygon (ghi đè in-place)'),
            [QgsProcessing.TypeVectorPolygon]
        ))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.P_LINES, self.tr('Lớp đường cắt (line)'),
            [QgsProcessing.TypeVectorLine]
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.P_SELECTED_ONLY, self.tr('Chỉ đối tượng được chọn'), defaultValue=False
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.P_PRESERVE, self.tr('Bảo toàn dữ liệu (phân phối theo diện tích)'), defaultValue=False
        ))
        self.addParameter(QgsProcessingParameterField(
            self.P_VALUE_FIELD, self.tr('Trường bảo toàn dữ liệu'),
            parentLayerParameterName=self.P_INPUT,
            type=QgsProcessingParameterField.Numeric,
            optional=True
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.P_RECALC_AREA, self.tr('Tính lại diện tích'), defaultValue=False
        ))
        self.addParameter(QgsProcessingParameterField(
            self.P_AREA_FIELD, self.tr('Trường diện tích (ghi lại)'),
            parentLayerParameterName=self.P_INPUT,
            type=QgsProcessingParameterField.Any,
            optional=True
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.P_AREA_UNITS_MODE,
            self.tr('Đơn vị diện tích khi ghi trường diện tích'),
            options=self.AREA_UNIT_OPTIONS,
            allowMultiple=False,
            defaultValue=0  # Theo Project
        ))

    # ===== Core =====
    def processAlgorithm(self, parameters, context, feedback):
        import processing  # import nội bộ

        lyr: QgsVectorLayer = self.parameterAsVectorLayer(parameters, self.P_INPUT, context)
        lines: QgsVectorLayer = self.parameterAsVectorLayer(parameters, self.P_LINES, context)
        selected_only = self.parameterAsBool(parameters, self.P_SELECTED_ONLY, context)
        preserve = self.parameterAsBool(parameters, self.P_PRESERVE, context)
        recalc_area = self.parameterAsBool(parameters, self.P_RECALC_AREA, context)
        value_field = self.parameterAsString(parameters, self.P_VALUE_FIELD, context)
        area_field = self.parameterAsString(parameters, self.P_AREA_FIELD, context)
        area_units_mode = self.parameterAsEnum(parameters, self.P_AREA_UNITS_MODE, context)

        if lyr is None or lines is None:
            raise QgsProcessingException(self.tr('Thiếu INPUT hoặc LINES'))
        if lyr.geometryType() != QgsWkbTypes.PolygonGeometry:
            raise QgsProcessingException(self.tr('INPUT phải là lớp Polygon'))
        if lines.geometryType() != QgsWkbTypes.LineGeometry:
            raise QgsProcessingException(self.tr('LINES phải là lớp Line'))

        if preserve and not value_field:
            feedback.reportError(self.tr("Bật 'Bảo toàn dữ liệu' nhưng chưa chọn trường số — bỏ qua Preserve."))
            preserve = False

        # 0) ÉP LỚP LINES VỀ CRS CỦA INPUT (rất quan trọng!)
        if lines.crs() != lyr.crs():
            feedback.pushInfo(self.tr(f"[CRS] Reproject LINES → {lyr.crs().authid()}"))
            try:
                reproj = processing.run(
                    'native:reprojectlayer',
                    {'INPUT': lines, 'TARGET_CRS': lyr.crs(), 'OPERATION': '', 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                    is_child_algorithm=True, context=context, feedback=feedback
                )
                out_obj = reproj.get('OUTPUT')
                if isinstance(out_obj, QgsVectorLayer):
                    lines_in = out_obj
                else:
                    lines_in = QgsProcessingUtils.mapLayerFromString(out_obj, context)
                    if lines_in is None or not isinstance(lines_in, QgsVectorLayer):
                        lines_in = QgsVectorLayer(out_obj, 'lines_reprojected', 'ogr')
                if not lines_in or not lines_in.isValid():
                    raise QgsProcessingException(self.tr('Reproject LINES thất bại.'))
                lines = lines_in
            except Exception as e:
                raise QgsProcessingException(self.tr(f'Không thể reproject LINES: {e!r}'))

        # 1) Xác định tập feature mục tiêu
        target_ids = set()
        if selected_only:
            target_ids = set(lyr.selectedFeatureIds())
        else:
            lines_extent = lines.extent()
            for f in lyr.getFeatures():
                if not f.geometry():
                    continue
                if not f.geometry().boundingBox().intersects(lines_extent):
                    continue
                hit = False
                for lf in lines.getFeatures():
                    if lf.geometry() and f.geometry().intersects(lf.geometry()):
                        hit = True
                        break
                if hit:
                    target_ids.add(f.id())

        if not target_ids:
            feedback.pushInfo(self.tr('Không có đối tượng nào bị đường cắt tác động.'))
            return {}

        # 2) Subset memory + __orig_id
        is_multi = QgsWkbTypes.isMultiType(lyr.wkbType())
        geom_str = 'MultiPolygon' if is_multi else 'Polygon'
        crs_authid = lyr.crs().authid()
        mem = QgsVectorLayer(f'{geom_str}?crs={crs_authid}', 'tmp_subset', 'memory')
        prov = mem.dataProvider()
        fields = lyr.fields()
        new_fields = QgsFields(fields)
        new_fields.append(QgsField('__orig_id', QVariant.LongLong))
        prov.addAttributes(new_fields)
        mem.updateFields()

        id_to_attrs = {}
        feats = []
        idx_orig = new_fields.indexFromName('__orig_id')
        for f in lyr.getFeatures():
            if f.id() in target_ids:
                nf = QgsFeature(new_fields)
                nf.setGeometry(f.geometry())
                attrs = f.attributes()
                for i in range(len(fields)):
                    nf[i] = attrs[i]
                nf[idx_orig] = f.id()
                feats.append(nf)
                id_to_attrs[f.id()] = attrs
        prov.addFeatures(feats)
        mem.updateExtents()

        # 3) Split (dùng LINES đã reproject)
        try:
            res = processing.run(
                'native:splitwithlines',
                {'INPUT': mem, 'LINES': lines, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                is_child_algorithm=True, context=context, feedback=feedback
            )
        except Exception as e:
            raise QgsProcessingException(self.tr(f'Lỗi khi chạy native:splitwithlines: {e!r}'))

        out_obj = res.get('OUTPUT')
        if isinstance(out_obj, QgsVectorLayer):
            out_lyr = out_obj
        else:
            out_lyr = QgsProcessingUtils.mapLayerFromString(out_obj, context)
            if out_lyr is None or not isinstance(out_lyr, QgsVectorLayer):
                out_lyr = QgsVectorLayer(out_obj, 'split_tmp', 'ogr')
                if not out_lyr.isValid():
                    raise QgsProcessingException(self.tr(f"Không nạp được OUTPUT từ splitwithlines: {out_obj!r}"))

        # 4) Gom nhóm theo __orig_id
        parts_by_orig = {}
        for pf in out_lyr.getFeatures():
            oid = pf['__orig_id']
            parts_by_orig.setdefault(oid, []).append(pf)

        if not any(len(v) >= 2 for v in parts_by_orig.values()):
            feedback.pushInfo(self.tr('Đường cắt không tạo ra phần tách (≥2) nào.'))
            return {}

        # 5) Thiết lập đo diện tích + đơn vị ghi ra
        d = QgsDistanceArea()
        d.setSourceCrs(out_lyr.crs(), QgsProject.instance().transformContext())
        # dùng ellipsoid của project nếu có
        try:
            ell = QgsProject.instance().ellipsoid()
            if ell:
                d.setEllipsoid(ell)
        except Exception:
            pass

        if area_units_mode == 0:
            # Theo Project
            if hasattr(QgsProject.instance(), 'areaUnits'):
                target_unit = QgsProject.instance().areaUnits()
            else:
                target_unit = QgsUnitTypes.AreaSquareMeters
        elif area_units_mode == 1:
            target_unit = QgsUnitTypes.AreaSquareMeters
        else:
            target_unit = QgsUnitTypes.AreaHectares

        try:
            factor_m2_to_target = QgsUnitTypes.fromUnitToUnitFactor(QgsUnitTypes.AreaSquareMeters, target_unit)
        except Exception:
            factor_m2_to_target = 1.0 if target_unit == QgsUnitTypes.AreaSquareMeters else 1.0 / 10000.0

        # 6) Chỉ số trường trên lớp gốc
        idx_area = lyr.fields().indexFromName(area_field) if (recalc_area and area_field) else -1
        idx_value = lyr.fields().indexFromName(value_field) if (preserve and value_field) else -1
        if preserve and idx_value < 0:
            feedback.reportError(self.tr("Không tìm thấy trường bảo toàn dữ liệu trên lớp đầu vào — bỏ qua Preserve."))
            preserve = False

        # 7) Sửa in-place
        started_edit = False
        if not lyr.isEditable():
            if not lyr.startEditing():
                raise QgsProcessingException(self.tr('Không thể mở chế độ chỉnh sửa cho lớp INPUT.'))
            started_edit = True

        del_ids = [oid for oid, plist in parts_by_orig.items() if len(plist) >= 2]
        if del_ids:
            lyr.deleteFeatures(del_ids)

        new_feats = []
        for oid, plist in parts_by_orig.items():
            if len(plist) < 2:
                continue
            base_attrs = id_to_attrs.get(oid)
            if base_attrs is None:
                continue

            # Tổng diện tích m² để chia tỉ lệ
            total_area_m2 = sum(d.measureArea(pf.geometry()) for pf in plist)

            v0 = None
            if preserve and idx_value >= 0:
                try:
                    v0 = float(base_attrs[idx_value])
                except Exception:
                    v0 = None

            acc_value = 0.0
            for i, pf in enumerate(plist):
                nf = QgsFeature(lyr.fields())
                nf.setGeometry(pf.geometry())
                nf.setAttributes(list(base_attrs))

                # Ghi diện tích theo đơn vị đã chọn
                if idx_area >= 0:
                    a_m2 = d.measureArea(pf.geometry())
                    a_val = a_m2 * factor_m2_to_target
                    nf.setAttribute(idx_area, a_val)

                # Phân phối giá trị theo tỉ lệ diện tích (đảm bảo tổng = v0)
                if preserve and idx_value >= 0 and v0 is not None:
                    part_area_m2 = d.measureArea(pf.geometry())
                    if i < len(plist) - 1 and total_area_m2 > 0:
                        v_part = v0 * (part_area_m2 / total_area_m2)
                        acc_value += v_part
                    else:
                        v_part = (v0 - acc_value) if v0 is not None else 0.0
                    nf.setAttribute(idx_value, v_part)

                new_feats.append(nf)

        if new_feats:
            lyr.addFeatures(new_feats)
        lyr.updateExtents()

        if started_edit:
            if not lyr.commitChanges():
                lyr.rollBack()
                raise QgsProcessingException(self.tr('Commit thay đổi thất bại cho lớp INPUT.'))
        else:
            lyr.triggerRepaint()

        feedback.pushInfo(self.tr(f'Đã tách {len(del_ids)} đối tượng; thêm {len(new_feats)} phần.'))
        return {}
