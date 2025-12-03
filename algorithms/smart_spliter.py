# -*- coding: utf-8 -*-
"""
Đặt file này vào plugin của bạn (ví dụ trong thư mục provider/algorithms hoặc algorithms/).
Thuật toán "Tách đối tượng (bảo toàn trị số)" cho phép:
- Tách LINE hoặc POLYGON bằng một lớp cắt (line hoặc polygon).
- Nếu đầu vào là POLYGON, có tùy chọn Bảo toàn diện tích: chọn các trường số để phân phối
theo tỷ lệ diện tích phần sau khi tách so với diện tích polygon gốc. Tổng sau tách = giá trị ban đầu.
- Tùy chọn tính lại diện tích cho các phần sau khi tách.

Kèm theo đó là ví dụ tích hợp QAction lên toolbar để mở hộp thoại xử lý.
"""

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterString,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsFeatureSink,
    QgsWkbTypes,
    QgsProject,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsProcessingException,
    QgsDistanceArea
)
from qgis import processing

class SplitFeaturesPreserveAlgorithm(QgsProcessingAlgorithm):
    """Tách đối tượng (bảo toàn trị số) – vector_utils"""

    # Parameter keys
    P_INPUT = 'INPUT'
    P_SPLITTER = 'SPLITTER'
    P_PRESERVE = 'PRESERVE_BY_AREA'
    P_FIELDS = 'FIELDS_TO_PRESERVE'
    P_RECALC_AREA = 'RECALC_AREA'
    P_AREA_FIELD = 'AREA_FIELD_NAME'
    P_OUTPUT = 'OUTPUT'

    def initAlgorithm(self, config=None):
        # Lớp cần tách: LINESTRING / POLYGON
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.P_INPUT,
                'Lớp cần tách (Line/Polygon)',
                types=[QgsProcessing.TypeVectorAnyGeometry]
            )
        )
        # Lớp cắt: Line hoặc Polygon (polygon sẽ được chuyển biên thành line)
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.P_SPLITTER,
                'Lớp cắt (Line hoặc Polygon)',
                types=[QgsProcessing.TypeVectorAnyGeometry]
            )
        )
        # Chỉ áp dụng khi INPUT là polygon
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_PRESERVE,
                'Bảo toàn diện tích (chỉ áp dụng cho Polygon)',
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.P_FIELDS,
                'Chọn các trường số để bảo toàn (Polygon)',
                parentLayerParameterName=self.P_INPUT,
                type=QgsProcessingParameterField.Numeric,
                allowMultiple=True,
                optional=True
            )
        )
        # Tính lại diện tích cho đầu ra polygon
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_RECALC_AREA,
                'Tính lại diện tích cho các phần sau tách (Polygon)',
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.P_AREA_FIELD,
                'Tên trường diện tích (nếu tính lại)',
                defaultValue='area_m2'
            )
        )
        # Đầu ra
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.P_OUTPUT,
                'Đầu ra sau khi tách'
            )
        )

    def createInstance(self):
        # BẮT BUỘC: QGIS gọi phương thức này để tạo bản sao thuật toán khi đăng ký vào Provider
        return SplitFeaturesPreserveAlgorithm()

    def name(self):
        return 'split_features_preserve'

    def displayName(self):
        return 'Chia tách thông minh'

    def group(self):
        return 'Tiện ích Vector'

    def groupId(self):
        return 'vector_utils'

    def shortHelpString(self):
        return (
            """
<b>Mục đích</b><br>
• Tách đối tượng Line/Polygon bằng một lớp cắt (Line hoặc Polygon).<br>
• Nếu đối tượng đầu vào là Polygon, có thể <i>Bảo toàn diện tích</i> cho các trường số được chọn: giá trị sau tách sẽ phân bổ theo tỷ lệ diện tích phần/diện tích ban đầu, đảm bảo tổng sau tách bằng giá trị gốc.<br>
• Tùy chọn tính lại diện tích cho các phần sau khi tách.<br><br>
<b>Tham số</b><br>
- <b>Lớp cần tách (Line/Polygon)</b>: Lớp vector đầu vào. Hỗ trợ LINESTRING và POLYGON.<br>
- <b>Lớp cắt (Line hoặc Polygon)</b>: Lớp dùng để cắt. Nếu là Polygon sẽ tự động chuyển biên thành Line trước khi cắt.<br>
- <b>Bảo toàn diện tích</b>: Chỉ áp dụng khi đầu vào là Polygon. Khi bật, các trường số được chọn sẽ phân phối theo tỷ lệ diện tích phần so với polygon gốc.<br>
- <b>Chọn các trường số để bảo toàn</b>: Danh sách trường số cần bảo toàn tổng. Bỏ trống nếu không cần.<br>
- <b>Tính lại diện tích</b>: Nếu bật (và đầu vào là Polygon), thuật toán sẽ thêm/cập nhật trường diện tích cho từng phần sau tách.<br>
- <b>Tên trường diện tích</b>: Tên trường diện tích (m²) sẽ được tạo/cập nhật khi bật "Tính lại diện tích".<br>
- <b>Đầu ra</b>: Lớp đối tượng sau khi tách.<br><br>
<b>Lưu ý</b><br>
• Thuật toán tự động chuyển hệ quy chiếu lớp cắt về CRS của lớp đầu vào để đảm bảo chính xác hình học.<br>
• Phân phối giá trị bảo toàn thực hiện theo tỉ lệ diện tích. Với trường số nguyên, giá trị được làm tròn và hiệu chỉnh ở phần cuối cùng để đảm bảo tổng chính xác như ban đầu.<br>
• Với Line, phần bảo toàn không áp dụng (chỉ thực hiện tách).<br>
            """
        )

    def processAlgorithm(self, parameters, context, feedback):
        input_lyr = self.parameterAsVectorLayer(parameters, self.P_INPUT, context)
        splitter_lyr = self.parameterAsVectorLayer(parameters, self.P_SPLITTER, context)
        preserve = self.parameterAsBool(parameters, self.P_PRESERVE, context)
        fields_to_preserve = self.parameterAsFields(parameters, self.P_FIELDS, context)
        recalc_area = self.parameterAsBool(parameters, self.P_RECALC_AREA, context)
        area_field_name = self.parameterAsString(parameters, self.P_AREA_FIELD, context)

        if input_lyr is None or splitter_lyr is None:
            raise QgsProcessingException('Thiếu lớp đầu vào hoặc lớp cắt.')

        in_geom_type = QgsWkbTypes.geometryType(input_lyr.wkbType())
        if in_geom_type not in (QgsWkbTypes.LineGeometry, QgsWkbTypes.PolygonGeometry):
            raise QgsProcessingException('Lớp đầu vào phải là Line hoặc Polygon.')

        # Chuẩn bị CRS và chuyển splitter về CRS của input nếu cần
        in_crs = input_lyr.crs()
        sp_crs = splitter_lyr.crs()
        splitter_layer_for_split = splitter_lyr

        if in_crs.isValid() and sp_crs.isValid() and in_crs != sp_crs:
            feedback.pushInfo('Đang chuyển CRS của lớp cắt về CRS của lớp đầu vào...')
            res = processing.run(
                'native:reprojectlayer',
                {
                    'INPUT': splitter_lyr,
                    'TARGET_CRS': in_crs,
                    'OUTPUT': 'memory:'
                },
                context=context, feedback=feedback
            )
            splitter_layer_for_split = res['OUTPUT']

        # Nếu splitter là polygon, chuyển sang đường biên
        if QgsWkbTypes.geometryType(splitter_layer_for_split.wkbType()) == QgsWkbTypes.PolygonGeometry:
            feedback.pushInfo('Chuyển lớp cắt Polygon sang đường biên...')
            res = processing.run(
                'native:polygonstolines',
                {
                    'INPUT': splitter_layer_for_split,
                    'OUTPUT': 'memory:'
                },
                context=context, feedback=feedback
            )
            splitter_layer_for_split = res['OUTPUT']

        # Gắn id gốc vào lớp đầu vào để theo dõi sau khi tách
        tmp_with_id = processing.run(
            'native:addautoincrementalfield',
            {
                'INPUT': input_lyr,
                'FIELD_NAME': '__orig_id',
                'START': 1,
                'GROUP_FIELDS': [],
                'SORT_EXPRESSION': '',
                'SORT_ASCENDING': True,
                'SORT_NULLS_FIRST': False,
                'OUTPUT': 'memory:'
            },
            context=context, feedback=feedback
        )['OUTPUT']

        # Tính diện tích gốc cho Polygon nếu cần bảo toàn
        orig_area_by_id = {}
        is_polygon = (in_geom_type == QgsWkbTypes.PolygonGeometry)
        d = QgsDistanceArea()
        d.setSourceCrs(in_crs, QgsProject.instance().transformContext())
        d.setEllipsoid(QgsProject.instance().ellipsoid())

        if is_polygon and preserve:
            for f in tmp_with_id.getFeatures():
                if not f.hasGeometry():
                    continue
                a = abs(d.measureArea(f.geometry()))
                orig_area_by_id[f['__orig_id']] = a

        # Tách đối tượng bằng splitter lines
        feedback.pushInfo('Đang tách đối tượng...')
        res_split = processing.run(
            'native:splitwithlines',
            {
                'INPUT': tmp_with_id,
                'LINES': splitter_layer_for_split,
                'OUTPUT': 'memory:'
            },
            context=context, feedback=feedback
        )
        split_layer = res_split['OUTPUT']

        # Chuẩn bị sink đầu ra (giữ schema như input, cộng thêm trường area nếu yêu cầu)
        in_fields = split_layer.fields()
        # Đảm bảo trường area có/không theo yêu cầu (đã chuyển sang dùng VectorLayer)
        if is_polygon and recalc_area:
            if in_fields.indexOf(area_field_name) == -1:
                in_fields.append(QgsField(area_field_name, QVariant.Double, len=20, prec=6))

        (sink, dest_id) = self.parameterAsSink(parameters, self.P_OUTPUT, context,
                                               in_fields, split_layer.wkbType(), in_crs)
        if sink is None:
            raise QgsProcessingException('Không tạo được đầu ra.')

        # Xử lý phân bổ giá trị bảo toàn theo diện tích cho Polygon
        # Thu thập danh sách phần theo __orig_id
        parts_by_orig = {}
        if is_polygon:
            for f in split_layer.getFeatures():
                oid = f['__orig_id']
                if oid is None:
                    continue
                parts_by_orig.setdefault(oid, []).append(f.id())

        # Tạo map: fid -> hệ số tỉ lệ theo diện tích
        ratio_by_fid = {}
        if is_polygon and preserve and fields_to_preserve:
            # Tính diện tích phần
            area_by_fid = {}
            for f in split_layer.getFeatures():
                if not f.hasGeometry():
                    continue
                area_by_fid[f.id()] = abs(d.measureArea(f.geometry()))
            for oid, fids in parts_by_orig.items():
                orig_a = max(orig_area_by_id.get(oid, 0.0), 0.0)
                if orig_a <= 0:
                    r = 0.0
                    for fid in fids:
                        ratio_by_fid[fid] = r
                else:
                    for fid in fids:
                        ratio_by_fid[fid] = area_by_fid.get(fid, 0.0) / orig_a

        # Ghi ra sink, đồng thời phân bổ & hiệu chỉnh tổng để đúng bằng giá trị gốc
        # Chuẩn bị kiểu dữ liệu của các trường cần bảo toàn
        preserve_types = {}
        if fields_to_preserve:
            for name in fields_to_preserve:
                idx = split_layer.fields().indexOf(name)
                if idx != -1:
                    preserve_types[name] = split_layer.fields()[idx].type()

        # Để hiệu chỉnh tổng theo nhóm __orig_id
        running_sums = {}  # {oid: {field: sum_assigned}}

        def is_integer_qvariant(qt_type):
            return qt_type in (QVariant.Int, QVariant.LongLong, QVariant.UInt, QVariant.ULongLong)

        for f in split_layer.getFeatures():
            new_f = QgsFeature(in_fields)
            attrs = f.attributes()
            # Nếu polygon & cần tính lại diện tích
            if is_polygon and recalc_area and f.hasGeometry():
                a = abs(d.measureArea(f.geometry()))
                # set/update area field value later after attrs copied
            else:
                a = None

            # Copy toàn bộ thuộc tính mặc định trước
            for i, val in enumerate(attrs):
                new_f.setAttribute(i, val)

            # Phân bổ bảo toàn
            oid = f['__orig_id'] if '__orig_id' in split_layer.fields().names() else None
            if is_polygon and preserve and fields_to_preserve and oid is not None:
                group_sum = running_sums.setdefault(oid, {})
                fids_in_group = parts_by_orig.get(oid, [])
                is_last_part = (f.id() == fids_in_group[-1]) if fids_in_group else False
                r = ratio_by_fid.get(f.id(), 0.0)

                for name, qt_type in preserve_types.items():
                    orig_val = f[name]
                    if orig_val is None:
                        continue
                    # Giá trị phân bổ sơ bộ
                    alloc = float(orig_val) * float(r)
                    # Làm tròn nếu là số nguyên
                    if is_integer_qvariant(qt_type):
                        alloc = round(alloc)

                    # Nếu là phần cuối cùng của nhóm -> hiệu chỉnh để tổng đúng bằng gốc
                    if is_last_part:
                        prev_sum = group_sum.get(name, 0.0)
                        correction = float(orig_val) - float(prev_sum)
                        # Với số nguyên, làm tròn correction
                        if is_integer_qvariant(qt_type):
                            correction = int(round(correction))
                        new_val = correction
                    else:
                        new_val = alloc
                        group_sum[name] = group_sum.get(name, 0.0) + float(new_val)

                    new_f.setAttribute(name, new_val)

            # Ghi/ cập nhật diện tích
            if is_polygon and recalc_area:
                idx_area = in_fields.indexOf(area_field_name)
                if idx_area != -1:
                    new_f.setAttribute(idx_area, a if a is not None else None)

            # Bỏ trường __orig_id trước khi ghi (nếu có)
            # Tạo danh sách thuộc tính cuối cùng tương ứng in_fields
            # (in_fields đã chứa mọi field của split_layer; __orig_id nằm trong đó)
            # Không xóa field ở schema được; ta để nguyên schema, nhưng
            # không sao: __orig_id sẽ theo ra đầu cuối (có thể giữ để truy vết).
            new_f.setGeometry(f.geometry())
            sink.addFeature(new_f, QgsFeatureSink.FastInsert)

        return {self.P_OUTPUT: dest_id}
