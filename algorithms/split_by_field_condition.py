# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
    QgsProcessingParameterString, QgsProcessingParameterFile,
    QgsProcessingParameterEnum, QgsProcessingParameterBoolean,
    QgsProcessingOutputString,
    QgsVectorLayer, QgsFields, QgsField, QgsFeature,
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
    QgsCoordinateReferenceSystem, QgsWkbTypes,
    QgsVectorFileWriter, QgsCoordinateTransformContext
)
import os
import re

def _tr(s):
    return QCoreApplication.translate("SplitByFieldConditionAlgorithm", s)

def _sanitize_filename(name: str) -> str:
    if name is None:
        return "empty"
    s = str(name).strip()
    if s == "":
        return "empty"
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^\w\-\.\(\)]", "_", s, flags=re.UNICODE)  # keep word/-/./()
    return s

class SplitByFieldConditionAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    SPLIT_FIELD = "SPLIT_FIELD"
    FILTER_EXPR = "FILTER_EXPR"
    SELECT_FIELDS = "SELECT_FIELDS"
    OUTPUT_DIR = "OUTPUT_DIR"
    DRIVER = "DRIVER"
    GROUP_TO_SINGLE_GPKG = "GROUP_TO_SINGLE_GPKG"
    SINGLE_GPKG_PATH = "SINGLE_GPKG_PATH"
    OUTPUT_SUMMARY = "OUTPUT_SUMMARY"

    DRIVER_SHP = 0
    DRIVER_GPKG = 1
    DRIVER_TAB = 2

    def name(self):
        return "split_by_field_condition"

    def displayName(self):
        return _tr("Tách lớp theo trường & điều kiện")

    def group(self):
        return _tr("Tiện ích Vector")

    def groupId(self):
        return "vector_utils"

    def shortHelpString(self):
        return _tr(
            "Tách lớp theo giá trị của 1 trường (vd: 'xa') và điều kiện lọc (vd: 'nggocr = 1').\n"
            "• Mỗi giá trị tạo một lớp, tên = giá trị trường đã làm sạch.\n"
            "• Chọn các trường cần xuất (nếu trống → tất cả).\n"
            "• CRS & encoding theo lớp đầu vào.\n"
            "• Xuất: ESRI Shapefile (.shp), GPKG (GeoPackage), MapInfo TAB (.tab).\n"
            "• Tuỳ chọn (chỉ với GPKG): Gộp tất cả lớp vào MỘT file GPKG duy nhất (mỗi lớp 1 layer)."
        )

    def createInstance(self):
        return SplitByFieldConditionAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, _tr("Lớp đầu vào"), [QgsProcessing.TypeVectorAnyGeometry]
        ))

        self.addParameter(QgsProcessingParameterField(
            self.SPLIT_FIELD, _tr("Trường tách (ví dụ: xa)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Any
        ))

        self.addParameter(QgsProcessingParameterString(
            self.FILTER_EXPR, _tr("Điều kiện (QGIS expression, ví dụ: nggocr = 1)"),
            defaultValue="", multiLine=False, optional=True
        ))

        # Multi-select fields (compat: enable if available; otherwise OK on 3.16)
        param_fields = QgsProcessingParameterField(
            self.SELECT_FIELDS, _tr("Các trường xuất (để trống = tất cả)"),
            parentLayerParameterName=self.INPUT,
            optional=True,
            type=QgsProcessingParameterField.Any
        )
        try:
            param_fields.setAllowMultiple(True)  # newer QGIS
        except Exception:
            pass
        self.addParameter(param_fields)

        # Thư mục đầu ra cho chế độ mỗi nhóm = 1 file riêng
        self.addParameter(QgsProcessingParameterFile(
            self.OUTPUT_DIR, _tr("Thư mục lưu kết quả (SHP/TAB hoặc nhiều GPKG)"),
            behavior=QgsProcessingParameterFile.Folder
        ))

        # Driver
        self.addParameter(QgsProcessingParameterEnum(
            self.DRIVER, _tr("Định dạng xuất"),
            options=["ESRI Shapefile", "GPKG (GeoPackage)", "MapInfo TAB"],
            defaultValue=self.DRIVER_SHP
        ))

        # Tuỳ chọn gộp 1 GPKG duy nhất (chỉ áp dụng khi DRIVER = GPKG)
        self.addParameter(QgsProcessingParameterBoolean(
            self.GROUP_TO_SINGLE_GPKG, _tr("Gộp tất cả lớp vào MỘT file GPKG duy nhất (mỗi lớp 1 layer)"),
            defaultValue=False
        ))

        # Đường dẫn file GPKG đích (khi gộp)
        self.addParameter(QgsProcessingParameterFile(
            self.SINGLE_GPKG_PATH, _tr("Đường dẫn file GPKG (khi gộp 1 file)"),
            behavior=QgsProcessingParameterFile.File, optional=True
        ))

        self.addOutput(QgsProcessingOutputString(self.OUTPUT_SUMMARY, _tr("Danh sách các lớp đã tạo")))

    # ---- helpers ----
    @staticmethod
    def _driver_and_ext(driver_idx):
        if driver_idx == SplitByFieldConditionAlgorithm.DRIVER_SHP:
            return "ESRI Shapefile", ".shp"
        if driver_idx == SplitByFieldConditionAlgorithm.DRIVER_GPKG:
            return "GPKG", ".gpkg"
        # MapInfo TAB
        return "MapInfo File", ".tab"

    @staticmethod
    def _create_writer(file_path, layer_name, fields, wkb_type, crs, encoding, driver_name, transform_context, overwrite_file=False):
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = driver_name
        opts.fileEncoding = encoding if encoding else "UTF-8"
        if driver_name.upper() == "GPKG":
            opts.layerName = layer_name
        # Hành vi khi file/layer đã tồn tại (giữ tương thích)
        try:
            opts.actionOnExistingFile = (
                QgsVectorFileWriter.CreateOrOverwriteFile if overwrite_file
                else QgsVectorFileWriter.CreateOrOverwriteLayer
            )
        except Exception:
            try:
                opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            except Exception:
                pass

        # QGIS < 3.22: trả (writer, err)
        # QGIS >= 3.22/3.44: trả trực tiếp QgsVectorFileWriter
        res = QgsVectorFileWriter.create(file_path, fields, wkb_type, crs, transform_context, opts)
        # Nếu là tuple (các bản cũ)
        if isinstance(res, tuple):
            writer, err = res
        else:
            writer, err = res, None  # bản mới chỉ trả object

        return writer, err


    def _read_selected_fields(self, parameters, context, src):
        try:
            sel = self.parameterAsFields(parameters, self.SELECT_FIELDS, context)
        except Exception:
            sel = None
        if not sel:
            return None
        if isinstance(sel, (list, tuple)):
            return [s for s in sel if s and src.fields().indexFromName(s) >= 0]
        if isinstance(sel, str):
            parts = [s.strip() for s in sel.split(",") if s.strip()]
            return [s for s in parts if src.fields().indexFromName(s) >= 0]
        return None

    def processAlgorithm(self, parameters, context, feedback):
        src = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None:
            raise QgsProcessingException(_tr("Không đọc được lớp đầu vào."))

        in_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if not isinstance(in_layer, QgsVectorLayer) or not in_layer.isValid():
            raise QgsProcessingException(_tr("Lớp đầu vào không hợp lệ."))

        split_field_name = self.parameterAsString(parameters, self.SPLIT_FIELD, context)
        if not split_field_name:
            raise QgsProcessingException(_tr("Bạn phải chọn trường để tách."))

        filter_expr_str = self.parameterAsString(parameters, self.FILTER_EXPR, context) or ""
        out_dir = self.parameterAsFile(parameters, self.OUTPUT_DIR, context)
        if not out_dir or not os.path.isdir(out_dir):
            raise QgsProcessingException(_tr("Thư mục đầu ra không hợp lệ."))

        driver_idx = self.parameterAsEnum(parameters, self.DRIVER, context)
        driver_name, ext = self._driver_and_ext(driver_idx)

        # Chế độ gộp 1 GPKG
        group_to_single_gpkg = self.parameterAsBoolean(parameters, self.GROUP_TO_SINGLE_GPKG, context)
        single_gpkg_path = self.parameterAsFile(parameters, self.SINGLE_GPKG_PATH, context) or ""

        if group_to_single_gpkg and driver_name.upper() != "GPKG":
            # Nếu người dùng bật gộp nhưng driver không phải GPKG → bỏ qua nhẹ nhàng
            group_to_single_gpkg = False

        # Nếu gộp 1 GPKG mà chưa chỉ file → tự đặt trong thư mục đầu ra
        if group_to_single_gpkg:
            if not single_gpkg_path:
                base = _sanitize_filename(os.path.splitext(os.path.basename(in_layer.name() or "output"))[0]) or "output"
                single_gpkg_path = os.path.join(out_dir, f"{base}.gpkg")
            # nếu file tồn tại → thêm hậu tố để tránh đè
            base0, ext0 = os.path.splitext(single_gpkg_path)
            i = 2
            path_try = single_gpkg_path
            while os.path.exists(path_try):
                path_try = f"{base0}_{i}{ext0}"
                i += 1
            single_gpkg_path = path_try

        # Lấy encoding theo lớp đầu vào
        encoding = in_layer.dataProvider().encoding() if in_layer.dataProvider() else "UTF-8"

        # index field tách
        fld_idx = src.fields().indexFromName(split_field_name)
        if fld_idx < 0:
            raise QgsProcessingException(_tr(f"Không tìm thấy trường '{split_field_name}' trong lớp đầu vào."))

        # danh sách field xuất
        selected_fields = self._read_selected_fields(parameters, context, src)
        if selected_fields:
            use_field_names = selected_fields
        else:
            use_field_names = [f.name() for f in src.fields()]

        use_field_indices = [src.fields().indexFromName(n) for n in use_field_names]
        out_fields = QgsFields()
        for i, n in zip(use_field_indices, use_field_names):
            fdef = src.fields().at(i)
            out_fields.append(QgsField(fdef.name(), fdef.type(), fdef.typeName(), fdef.length(), fdef.precision()))

        # expression filter
        expr = None
        if filter_expr_str.strip():
            expr = QgsExpression(filter_expr_str)
            if expr.hasParserError():
                raise QgsProcessingException(_tr(f"Biểu thức không hợp lệ: {expr.parserErrorString()}"))

        ctx = QgsExpressionContext()
        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(in_layer))

        # Writers
        writers = {}  # group_name -> (writer, path, layer_name)
        counts = {}

        crs = in_layer.sourceCrs() if in_layer.sourceCrs().isValid() else QgsCoordinateReferenceSystem()
        wkb = QgsWkbTypes.multiType(in_layer.wkbType())
        wkb = QgsWkbTypes.dropZ(QgsWkbTypes.dropM(wkb))
        tctx = context.transformContext() if hasattr(context, "transformContext") else QgsCoordinateTransformContext()

        created = []

        # Nếu gộp 1 GPKG, cần tạo/lần đầu ghi file (layer sẽ tạo dần)
        gpkg_file_path = single_gpkg_path if group_to_single_gpkg else None
        first_layer = True  # để quyết định overwrite file hay chỉ overwrite layer

        total = src.featureCount() or 1
        for k, f in enumerate(in_layer.getFeatures(), start=1):
            if k % 1000 == 0:
                feedback.setProgress(int(100.0 * k / total))

            if expr is not None:
                ctx.setFeature(f)
                val = expr.evaluate(ctx)
                if expr.hasEvalError():
                    raise QgsProcessingException(_tr(f"Lỗi evaluate biểu thức tại FID {f.id()}: {expr.evalErrorString()}"))
                if not bool(val):
                    continue

            key_val = f[fld_idx]
            if key_val is None:
                continue

            group_name = _sanitize_filename(key_val)

            # mở writer nếu chưa có
            if group_name not in writers:
                if group_to_single_gpkg:
                    file_path = gpkg_file_path
                    layer_name = group_name
                    overwrite_file = first_layer  # layer đầu tiên: tạo file (overwrite nếu cần)
                    first_layer = False
                else:
                    file_path = os.path.join(out_dir, f"{group_name}{ext}")
                    layer_name = group_name
                    # tránh đè file ở chế độ nhiều file
                    base, ext0 = os.path.splitext(file_path)
                    suffix = 2
                    while os.path.exists(file_path):
                        file_path = f"{base}_{suffix}{ext0}"
                        if driver_name.upper() == "GPKG":
                            layer_name = f"{group_name}_{suffix}"
                        suffix += 1
                    overwrite_file = True  # file mới

                writer, err = self._create_writer(
                    file_path=file_path,
                    layer_name=layer_name,
                    fields=out_fields,
                    wkb_type=wkb,
                    crs=crs,
                    encoding=encoding,
                    driver_name=("GPKG" if group_to_single_gpkg else driver_name),
                    transform_context=tctx,
                    overwrite_file=overwrite_file
                )
                if writer is None:
                    raise QgsProcessingException(_tr(f"Không tạo được writer cho nhóm '{group_name}': {err}"))

                writers[group_name] = (writer, file_path, layer_name)
                counts[group_name] = 0
                if file_path not in created:
                    created.append(file_path)

            writer, _, _ = writers[group_name]

            new_f = QgsFeature(out_fields)
            new_f.setGeometry(f.geometry())
            attrs = []
            src_attrs = f.attributes()
            for idx in use_field_indices:
                attrs.append(src_attrs[idx] if idx < len(src_attrs) else None)
            new_f.setAttributes(attrs)

            if not writer.addFeature(new_f):
                if not writer.addFeature(new_f):
                    raise QgsProcessingException(_tr(f"Không ghi được feature vào '{group_name}' (FID {f.id()})."))

            counts[group_name] += 1

        # đóng writer
        for key, (w, _p, _ln) in writers.items():
            del w  # flush

        # Tổng kết
        if not created:
            summary = _tr("Không tạo lớp nào (không có feature thoả điều kiện).")
        else:
            if group_to_single_gpkg:
                lines = [f"{k}: {counts[k]} features" for k in sorted(counts.keys())]
                summary = _tr("Đã tạo {n} layer trong GPKG:\n").format(n=len(counts)) + "\n".join(lines) + f"\nFile: {gpkg_file_path}"
            else:
                lines = [f"{k}: {counts[k]} features" for k in sorted(counts.keys())]
                summary = _tr("Đã tạo {n} lớp (file):\n").format(n=len(counts)) + "\n".join(lines)

        return {self.OUTPUT_SUMMARY: summary}
