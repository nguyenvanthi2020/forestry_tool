# -*- coding: utf-8 -*-
"""
Merge Validated Vectors (force Int32, cap width=7; decimals stay Double) — QGIS 3.16+
Tác giả: bạn & ChatGPT

Chính sách kiểu:
- Numeric:
  • Nếu BẤT KỲ lớp nào có trường thập phân (Double) → ĐẦU RA Double, length/precision = MAX các lớp.
  • Nếu TẤT CẢ là số nguyên → ĐẦU RA Int32 (QVariant.Int), length = MIN(MAX các lớp, 7).  ✅
- Non-numeric: TYPE phải đồng nhất giữa các lớp; length/precision đầu ra = MAX.
- Không hạ kiểu thập phân về nguyên khi ghi; kiểm tra phần thập phân khi đầu ra là Int32.
- Kiểm tra phạm vi Int32 khi ghi (±2,147,483,648).
- Log chi tiết: schema từng lớp, CRS, promotion & capping width.

Tương thích: QGIS 3.16+
"""

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingParameterMultipleLayers, QgsProcessingParameterCrs,
    QgsProcessingParameterFeatureSink, QgsProcessingException,
    QgsFeature, QgsFields, QgsField, QgsWkbTypes,
    QgsCoordinateTransform, QgsCoordinateTransformContext
)

class MergeValidatedVectors(QgsProcessingAlgorithm):
    INPUTS = 'INPUTS'
    OUTPUT_CRS = 'OUTPUT_CRS'
    OUTPUT = 'OUTPUT'

    INT32_MAX_WIDTH = 7  # độ rộng tối đa cho trường integer

    def tr(self, text):
        return QCoreApplication.translate('MergeValidatedVectors', text)

    def createInstance(self):
        return MergeValidatedVectors()

    def name(self):
        return 'merge_validated_vectors'

    def displayName(self):
        return self.tr('Ghép lớp bản đồ (nghiêm ngặt)')

    def group(self):
        return self.tr('Tiện ích Vector')

    def groupId(self):
        return 'vector_utils'

    def shortHelpString(self):
        return self.tr(
            'Ghép nhiều lớp khi tên & thứ tự trường trùng nhau; hình học/CRS nhất quán.\n'
            '- Nếu có decimal: đầu ra Double (len/prec = MAX)\n'
            '- Nếu toàn bộ là số nguyên: đầu ra Int32 với width = min(MAX, 7)\n'
            '- Non-numeric: TYPE đồng nhất; len/prec = MAX\n'
            '- Kiểm tra phần thập phân & phạm vi Int32 khi ghi thuộc tính.'
        )

    # ---------- Helpers ----------
    _NUMERIC_QVARIANT = {QVariant.Int, QVariant.UInt, QVariant.LongLong, QVariant.ULongLong, QVariant.Double}

    def _is_numeric(self, qvariant_type_id: int) -> bool:
        return qvariant_type_id in self._NUMERIC_QVARIANT

    def _format_fields_for_log(self, fields: QgsFields):
        lines = []
        for i in range(fields.count()):
            f = fields.at(i)
            lines.append(
                f"  - {i+1}. {f.name()} | type={f.typeName()}(id={f.type()}) | len={f.length()} | prec={f.precision()}"
            )
        return "\n".join(lines)

    def _collect_schema(self, layers, feedback):
        """
        Kiểm tra: số trường & tên theo THỨ TỰ giống nhau.
        Tạo sink_fields & metadata:
          - Numeric: nếu any_decimal → Double (len/prec = MAX), else → Int32 (len = min(MAX,7)).
          - Non-numeric: giữ TYPE; len/prec = MAX.
        Trả: (sink_fields, decimal_out_by_name)
        """
        base_fields = layers[0].fields()
        n = base_fields.count()

        # Kiểm số trường & tên theo thứ tự
        for lyr in layers[1:]:
            flds = lyr.fields()
            if flds.count() != n:
                raise QgsProcessingException(self.tr(
                    f"Số trường khác nhau giữa '{layers[0].name()}' ({n}) và '{lyr.name()}' ({flds.count()})."
                ))
            for i in range(n):
                if base_fields.at(i).name() != flds.at(i).name():
                    raise QgsProcessingException(self.tr(
                        f"Tên trường khác nhau tại vị trí {i+1}: "
                        f"'{base_fields.at(i).name()}' vs '{flds.at(i).name()}' (lớp {lyr.name()})."
                    ))

        # Log input layers
        feedback.pushInfo('=== THÔNG TIN LỚP ĐẦU VÀO ===')
        for idx, lyr in enumerate(layers, start=1):
            crs = lyr.crs().authid() if lyr.crs().isValid() else '(no CRS)'
            gtype = QgsWkbTypes.displayString(layers[0].wkbType())
            feedback.pushInfo(f"[{idx}] {lyr.name()} | Geo: {gtype} | CRS: {crs} | Trường: {lyr.fields().count()}")
            feedback.pushInfo(self._format_fields_for_log(lyr.fields()))

        sink_fields = QgsFields()
        decimal_out_by_name = {}
        promo_log = []

        for i in range(n):
            name = base_fields.at(i).name()
            types, lens, precs = [], [], []
            for lyr in layers:
                f = lyr.fields().at(i)
                types.append(f.type())
                lens.append(max(0, f.length()))
                precs.append(max(0, f.precision()))

            max_len = max(lens) if lens else 0
            max_prec = max(precs) if precs else 0

            all_numeric = all(self._is_numeric(t) for t in types)
            any_decimal = any(t == QVariant.Double for t in types)

            base_f = base_fields.at(i)

            if all_numeric:
                if any_decimal:
                    # Decimal: giữ Double, len/prec = MAX
                    out_len = max_len
                    out_prec = max_prec
                    sink_fields.append(QgsField(name, QVariant.Double, 'Double', out_len, out_prec))
                    decimal_out_by_name[name] = True
                    promo_log.append(f"  - {name}: NUMERIC→Double, len={out_len}, prec={out_prec} (MAX)")
                else:
                    # Toàn bộ integer: ép Int32, width = min(MAX, 7)
                    out_len = min(max_len if max_len > 0 else self.INT32_MAX_WIDTH, self.INT32_MAX_WIDTH)
                    f_out = QgsField(name, QVariant.Int, 'Integer', out_len, 0)
                    # Thử đặt SubType=Int32 nếu API có (QGIS ≥ ~3.22)
                    try:
                        if hasattr(f_out, 'setSubType'):
                            from qgis.core import QgsField as _QF
                            if hasattr(_QF, 'SubType') and hasattr(_QF.SubType, 'Int32'):
                                f_out.setSubType(_QF.SubType.Int32)
                    except Exception:
                        pass
                    sink_fields.append(f_out)
                    decimal_out_by_name[name] = False
                    promo_log.append(f"  - {name}: INTEGER→Int32, len={out_len} (cap at {self.INT32_MAX_WIDTH})")
            else:
                # Non-numeric: TYPE phải đồng nhất; len/prec = MAX
                base_type = base_f.type()
                if any(t != base_type for t in types):
                    raise QgsProcessingException(self.tr(
                        f"Trường '{name}' không đồng nhất TYPE giữa các lớp. "
                        f"Chỉ cho phép khác biệt trong phạm vi CÁC KIỂU SỐ."
                    ))
                out_len = max_len
                out_prec = max_prec
                sink_fields.append(QgsField(name, base_type, base_f.typeName(), out_len, out_prec))
                decimal_out_by_name[name] = False
                promo_log.append(f"  - {name}: NON-NUMERIC giữ TYPE {base_f.typeName()}, len={out_len}, prec={out_prec} (MAX)")

        feedback.pushInfo('=== HỢP NHẤT KIỂU & ĐỘ RỘNG TRƯỜNG (INTEGER→Int32 width≤7) ===')
        for line in promo_log:
            feedback.pushInfo(line)

        return sink_fields, decimal_out_by_name

    # ---------- QGIS API ----------
    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.INPUTS,
                self.tr('Các lớp vector đầu vào (>= 2)'),
                layerType=QgsProcessing.TypeVectorAnyGeometry
            )
        )
        self.addParameter(
            QgsProcessingParameterCrs(
                self.OUTPUT_CRS,
                self.tr('Hệ tọa độ đầu ra (tùy chọn, mặc định theo lớp đầu vào thứ nhất)'),
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr('Lớp ghép đầu ra')
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        layers = self.parameterAsLayerList(parameters, self.INPUTS, context)
        if not layers or len(layers) < 2:
            raise QgsProcessingException(self.tr('Cần chọn ít nhất 2 lớp vector.'))

        # Kiểm tra loại hình học (kể cả Z/M)
        base_geom = layers[0].wkbType()
        for lyr in layers[1:]:
            if (QgsWkbTypes.geometryType(lyr.wkbType()) != QgsWkbTypes.geometryType(base_geom)
                or QgsWkbTypes.hasZ(lyr.wkbType()) != QgsWkbTypes.hasZ(base_geom)
                or QgsWkbTypes.hasM(lyr.wkbType()) != QgsWkbTypes.hasM(base_geom)):
                raise QgsProcessingException(self.tr(
                    f"Loại hình học khác nhau giữa '{layers[0].name()}' và '{lyr.name()}'."
                ))

        # Schema & mapping
        sink_fields, decimal_out_by_name = self._collect_schema(layers, feedback)

        # CRS đầu ra
        out_crs = self.parameterAsCrs(parameters, self.OUTPUT_CRS, context)
        if not out_crs or not out_crs.isValid():
            out_crs = layers[0].crs()
            feedback.pushInfo(self.tr(f'Không chọn CRS đầu ra ⇒ dùng CRS lớp đầu vào thứ nhất: {out_crs.authid()}'))
        else:
            feedback.pushInfo(self.tr(f'CRS đầu ra: {out_crs.authid()}'))

        # Tạo sink
        (sink, dest_id) = self.parameterAsSink(parameters, self.OUTPUT, context, sink_fields, base_geom, out_crs)
        if sink is None:
            raise QgsProcessingException(self.tr('Không tạo được lớp đầu ra.'))

        # Transform context
        ct_ctx: QgsCoordinateTransformContext = context.transformContext()

        # Ghi dữ liệu
        total = sum([lyr.featureCount() for lyr in layers])
        processed = 0

        for lyr in layers:
            lyr_crs = lyr.crs()
            need_reproj = (lyr_crs.isValid() and out_crs.isValid() and lyr_crs != out_crs)
            xform = QgsCoordinateTransform(lyr_crs, out_crs, ct_ctx) if need_reproj else None

            src_name_to_idx = {lyr.fields().at(i).name(): i for i in range(lyr.fields().count())}

            for feat in lyr.getFeatures():
                if feedback.isCanceled():
                    break

                new_feat = QgsFeature(sink_fields)

                # Hình học (reproject nếu cần)
                geom = feat.geometry()
                if not geom.isNull() and xform:
                    try:
                        g = geom
                        g.transform(xform)
                        geom = g
                    except Exception as e:
                        raise QgsProcessingException(self.tr(
                            f'Lỗi chuyển CRS feature ID {feat.id()} của lớp "{lyr.name()}": {e}'
                        ))
                new_feat.setGeometry(geom)

                # Thuộc tính: Double vs Int32 (width đã cap ở schema); không hạ kiểu thập phân
                out_attrs = [None] * sink_fields.count()
                for sink_idx in range(sink_fields.count()):
                    out_field = sink_fields.at(sink_idx)
                    name = out_field.name()
                    src_idx = src_name_to_idx.get(name, None)
                    if src_idx is None:
                        raise QgsProcessingException(self.tr(
                            f'Không tìm thấy trường "{name}" trong lớp "{lyr.name()}".'
                        ))

                    val = feat.attributes()[src_idx]
                    if val is not None:
                        if out_field.type() == QVariant.Double or decimal_out_by_name.get(name, False):
                            # ĐẦU RA Double: giữ phần thập phân (không ép về nguyên)
                            try:
                                val = float(val) if isinstance(val, (int, float)) else float(str(val))
                            except Exception:
                                val = None
                        elif out_field.type() == QVariant.Int:
                            # ĐẦU RA Int32: cấm giá trị có phần thập phân; kiểm tra phạm vi
                            try:
                                if isinstance(val, float):
                                    if val % 1 != 0:
                                        raise QgsProcessingException(self.tr(
                                            f'Phát hiện giá trị thập phân ({val}) ở trường "{name}" '
                                            f'nhưng đầu ra là Int32. Vui lòng chuyển trường sang Double.'
                                        ))
                                    val = int(val)
                                elif isinstance(val, int):
                                    pass
                                else:
                                    fval = float(str(val))
                                    if fval % 1 != 0:
                                        raise QgsProcessingException(self.tr(
                                            f'Phát hiện giá trị thập phân ("{val}") ở trường "{name}" '
                                            f'nhưng đầu ra là Int32.'
                                        ))
                                    val = int(fval)

                                # Phạm vi Int32
                                if val < -2147483648 or val > 2147483647:
                                    raise QgsProcessingException(self.tr(
                                        f'Giá trị {val} ở trường "{name}" vượt phạm vi Int32. '
                                        f'Vui lòng chuyển trường sang Double hoặc chuẩn hóa dữ liệu.'
                                    ))
                            except QgsProcessingException:
                                raise
                            except Exception:
                                val = None
                        else:
                            # Non-numeric: giữ nguyên
                            pass

                    out_attrs[sink_idx] = val

                new_feat.setAttributes(out_attrs)
                sink.addFeature(new_feat)

                processed += 1
                if total and (processed % 1000 == 0):
                    feedback.setProgress(int(processed * 100.0 / total))

        # Tổng kết
        feedback.pushInfo(self.tr('=== HOÀN THÀNH GHÉP LỚP ==='))
        feedback.pushInfo(self.tr(f'Tổng số đối tượng ghi: {processed}'))
        feedback.pushInfo(self.tr(f'Loại hình học: {QgsWkbTypes.displayString(base_geom)}'))
        feedback.pushInfo(self.tr(f'CRS đầu ra: {out_crs.authid()}'))
        feedback.pushInfo(self.tr('Schema đầu ra (INTEGER→Int32 width≤7; DECIMAL giữ Double):'))
        feedback.pushInfo(self._format_fields_for_log(sink_fields))

        return {self.OUTPUT: dest_id}
