# -*- coding: utf-8 -*-
# QGIS 3.16+ compatible
# File: Forestry_tool/algorithms/split_draw_controller.py

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import Qt, QVariant, pyqtSignal
from qgis.PyQt.QtGui import QColor

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsWkbTypes, QgsField, QgsFeature,
    QgsGeometry, QgsCoordinateTransform, QgsPointXY, QgsPointLocator,
    QgsTolerance, QgsProcessing, QgsFeatureRequest, QgsRectangle,
    QgsSpatialIndex, QgsCoordinateReferenceSystem
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
import processing
import math
import heapq


ALG_ID = 'forestry_tool:split_polygons_inplace_by_lines'


# ---------- Helpers chung ----------
def _mk_field(name, vartype):
    """Tạo QgsField tương thích 3.16..3.44 để tránh DeprecationWarning."""
    try:
        return QgsField(name, type=vartype)  # API mới
    except TypeError:
        return QgsField(name, vartype)       # API cũ


# ---------------- Dockable Widget cấu hình ----------------
class SplitConfigWidget(QtWidgets.QWidget):
    startRequested = pyqtSignal()
    stopRequested = pyqtSignal()
    applyRequested = pyqtSignal()

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setObjectName("ForestrySplitConfigWidget")

        outer = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        outer.addLayout(form)

        # 1) TẠO TẤT CẢ WIDGET TRƯỚC
        self.cmbLayer = QtWidgets.QComboBox()
        self.layers = []
        form.addRow("Lớp polygon:", self.cmbLayer)

        pick_row = QtWidgets.QHBoxLayout()
        self.btnPickActive = QtWidgets.QPushButton("Chọn từ lớp đang Active")
        self.btnRefresh = QtWidgets.QPushButton("Làm mới danh sách")
        pick_row.addWidget(self.btnPickActive)
        pick_row.addWidget(self.btnRefresh)
        outer.addLayout(pick_row)

        self.cmbValue = QtWidgets.QComboBox()
        form.addRow("Trường bảo toàn dữ liệu:", self.cmbValue)

        self.cmbArea = QtWidgets.QComboBox()
        self.cmbArea.addItem("(không)")
        form.addRow("Trường diện tích (ghi lại):", self.cmbArea)

        self.chkSelected = QtWidgets.QCheckBox("Chỉ đối tượng được chọn")
        self.chkPreserve = QtWidgets.QCheckBox("Bảo toàn dữ liệu (phân phối theo diện tích)")
        self.chkArea = QtWidgets.QCheckBox("Tính lại diện tích")
        outer.addWidget(self.chkSelected)
        outer.addWidget(self.chkPreserve)
        outer.addWidget(self.chkArea)

        unit_row = QtWidgets.QHBoxLayout()
        unit_row.addWidget(QtWidgets.QLabel("Đơn vị diện tích khi ghi:"))
        self.cmbAreaUnit = QtWidgets.QComboBox()
        self.cmbAreaUnit.addItems(["Theo Project", "m²", "hecta"])
        unit_row.addWidget(self.cmbAreaUnit)
        unit_row.addStretch(1)
        outer.addLayout(unit_row)

        snap_row = QtWidgets.QHBoxLayout()
        self.chkSnap = QtWidgets.QCheckBox("Bật Snapping")
        self.chkSnap.setChecked(True)
        snap_row.addWidget(self.chkSnap)
        snap_row.addStretch(1)
        snap_row.addWidget(QtWidgets.QLabel("Tolerance (px):"))
        self.spinSnapPx = QtWidgets.QSpinBox()
        self.spinSnapPx.setRange(1, 100)
        self.spinSnapPx.setValue(12)
        snap_row.addWidget(self.spinSnapPx)
        outer.addLayout(snap_row)

        self.chkTrace = QtWidgets.QCheckBox("Bật Tracing (tự động bám biên/line)")
        self.chkTrace.setChecked(True)
        outer.addWidget(self.chkTrace)

        self.lblStatus = QtWidgets.QLabel("<i>Chưa bắt đầu vẽ</i>")
        outer.addWidget(self.lblStatus)

        btn_row = QtWidgets.QHBoxLayout()
        self.btnStart = QtWidgets.QPushButton("Bắt đầu vẽ")
        self.btnApply = QtWidgets.QPushButton("Áp dụng cấu hình")
        self.btnStop = QtWidgets.QPushButton("Dừng vẽ")
        btn_row.addWidget(self.btnStart)
        btn_row.addWidget(self.btnApply)
        btn_row.addWidget(self.btnStop)
        outer.addLayout(btn_row)

        outer.addStretch(1)

        # 2) GẮN SIGNALS
        self.cmbLayer.currentIndexChanged.connect(self._reload_fields)
        self.btnRefresh.clicked.connect(self._reload_layers)
        self.btnPickActive.clicked.connect(self._pick_active_layer)
        self.btnStart.clicked.connect(self.startRequested.emit)
        self.btnStop.clicked.connect(self.stopRequested.emit)
        self.btnApply.clicked.connect(self.applyRequested.emit)

        # 3) SAU KHI CÓ ĐỦ WIDGET MỚI GỌI RELOAD
        self._reload_layers()
        if self.layers:
            self._reload_fields(self.cmbLayer.currentIndex())


    # ---- public API ----
    def layer(self):
        return self.layers[self.cmbLayer.currentIndex()] if self.layers else None

    def value_field(self):
        return self.cmbValue.currentText() if self.cmbValue.count() else None

    def area_field(self):
        t = self.cmbArea.currentText()
        return None if (not t or t == "(không)") else t

    def options(self):
        return dict(
            selected_only=self.chkSelected.isChecked(),
            preserve=self.chkPreserve.isChecked(),
            recalc_area=self.chkArea.isChecked(),
            area_units_mode=self.cmbAreaUnit.currentIndex(),  # 0=project,1=m2,2=ha
            snapping=self.chkSnap.isChecked(),
            snap_px=self.spinSnapPx.value(),
            tracing=self.chkTrace.isChecked()
        )

    def setStatus(self, text, good=False):
        color = "#0a0" if good else "#666"
        self.lblStatus.setText(f'<span style="color:{color}">{text}</span>')

    # ---- internals ----
    def _reload_layers(self):
        self.layers = []
        self.cmbLayer.blockSignals(True)
        self.cmbLayer.clear()
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
                self.layers.append(lyr)
                self.cmbLayer.addItem(lyr.name())
        self.cmbLayer.blockSignals(False)
        if self.layers:
            self._reload_fields(self.cmbLayer.currentIndex())

    def _reload_fields(self, idx):
        self.cmbValue.clear()
        self.cmbArea.clear()
        self.cmbArea.addItem("(không)")
        if not self.layers:
            return
        lyr = self.layers[idx]
        for fld in lyr.fields():
            if fld.type() in (QVariant.Int, QVariant.Double, QVariant.LongLong, QVariant.UInt, QVariant.ULongLong):
                self.cmbValue.addItem(fld.name())
            self.cmbArea.addItem(fld.name())

    def _pick_active_layer(self):
        lyr = self.iface.activeLayer()
        if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
            for i, l in enumerate(self.layers):
                if l.id() == lyr.id():
                    self.cmbLayer.setCurrentIndex(i)
                    return
            self._reload_layers()
            for i, l in enumerate(self.layers):
                if l.id() == lyr.id():
                    self.cmbLayer.setCurrentIndex(i)
                    return
        else:
            QtWidgets.QMessageBox.information(self, "Thông báo", "Lớp active hiện tại không phải Polygon.")


# ---------------- Map Tool ----------------
class DrawLineTool(QgsMapTool):
    """
    Tracing động theo **canonical trace layer**:
    - Dù snap vào polygon gốc, boundary tạm hay line, đều quy về một *canonical* layer dạng line.
      Vì vậy 2 điểm cùng biên polygon luôn dùng chung lớp → đường sẽ ép theo biên.
    - Thứ tự tìm đường: substring (cùng feature) → QgsTracer (nếu có) → Dijkstra trên đồ thị biên (fallback).
    - Có ghost, Backspace, ESC, và marker tím tại điểm snap.
    """
    def __init__(self, iface, on_finish, opts, target_polygon_layer):
        super().__init__(iface.mapCanvas())
        self.iface = iface
        self.on_finish = on_finish
        self.opts = opts

        self.enable_snapping = bool(opts.get('snapping', True))
        self.snap_px = int(opts.get('snap_px', 12))
        self.enable_tracing = bool(opts.get('tracing', True))
        self.selected_only = bool(opts.get('selected_only', False))

        self.target_polygon_layer = target_polygon_layer

        # dữ liệu vẽ
        self.points = []         # list[QgsPointXY]
        self.points_info = []    # list[dict{pt, snap_layer, snap_fid, trace_layer}]

        # hiển thị
        self.rb = QgsRubberBand(self.canvas(), QgsWkbTypes.LineGeometry)
        self.rb.setWidth(2)
        self.rb.setColor(QColor(0, 120, 255, 220))

        self.rbGhost = QgsRubberBand(self.canvas(), QgsWkbTypes.LineGeometry)
        self.rbGhost.setWidth(1)
        self.rbGhost.setColor(QColor(20, 20, 20, 120))

        self.snap_marker = QgsVertexMarker(self.canvas())
        icon_square = getattr(QgsVertexMarker, 'ICON_BOX', getattr(QgsVertexMarker, 'ICON_CROSS', 0))
        self.snap_marker.setIconType(icon_square)
        self.snap_marker.setColor(QColor(160, 32, 240))
        self.snap_marker.setFillColor(QColor(160, 32, 240, 160))
        self.snap_marker.setIconSize(8)
        self.snap_marker.setPenWidth(2)
        self.snap_marker.setZValue(10000)
        self.snap_marker.hide()

        # lớp snap/trace
        self.snap_layers = []
        self.trace_layers = []
        self.boundary_layer = None
        self.boundary_unified = None
        self.other_poly_boundaries = []
        self._temp_layers = []

        # cache
        self._layer_unified_cache = {}   # {layer.id(): unified_line_layer}
        self._trace_source_cache = {}    # {layer.id(): trace_line_layer}
        self._spatial_index_cache = {}   # {trace_layer.id(): QgsSpatialIndex}
        self._graph_cache = {}           # graph cache cho fallback đồ thị

        # ánh xạ mọi layer → canonical trace layer (line)
        self._canonical_map = {}         # {any_layer_id: canonical_line_layer}

        # tracer (optional)
        self.tracer = None
        self._have_qgstracer = False

        self._using_canvas_snapping = False
        self._hover_info = None  # dict{pt, snap_layer, snap_fid, trace_layer}

    # Cho phép cập nhật nhanh khi user bấm "Áp dụng" trong Dock
    def reload(self, new_layer, new_opts):
        self.target_polygon_layer = new_layer
        self.opts = dict(self.opts, **(new_opts or {}))
        self.enable_snapping = bool(self.opts.get('snapping', True))
        self.snap_px = int(self.opts.get('snap_px', 12))
        self.enable_tracing = bool(self.opts.get('tracing', True))
        self.selected_only = bool(self.opts.get('selected_only', False))

        # reset trạng thái vẽ để tránh trộn CRS/đồ thị cũ
        self.points.clear()
        self.points_info.clear()
        self.rb.reset(QgsWkbTypes.LineGeometry)
        self.rbGhost.reset(QgsWkbTypes.LineGeometry)
        self.snap_marker.hide()

        # rebuild môi trường trace/snap
        self._cleanup_temps()
        self._canonical_map.clear()
        self._layer_unified_cache.clear()
        self._spatial_index_cache.clear()
        self._graph_cache.clear()
        self._prepare_snap_and_trace_layers()
        self._setup_canvas_snapping()
        if self.enable_tracing:
            self._init_tracer()

    def _cleanup_temps(self):
        try:
            for lyr in self._temp_layers:
                QgsProject.instance().removeMapLayer(lyr.id())
        except Exception:
            pass
        self._temp_layers = []

    # lifecycle
    def activate(self):
        super().activate()
        self.canvas().setCursor(Qt.CrossCursor)
        self._reset_state()

        self._prepare_snap_and_trace_layers()
        self._setup_canvas_snapping()
        if self.enable_tracing:
            self._init_tracer()
            self.iface.messageBar().pushInfo(
                "Tracing",
                "Nếu 2 điểm cùng một lớp (kể cả polygon), đường vẽ sẽ ép theo chính lớp đó."
            )

    def deactivate(self):
        super().deactivate()
        self._using_canvas_snapping = False
        self._clear_rb()
        self.snap_marker.hide()
        self._cleanup_temps()
        self.other_poly_boundaries = []
        self._layer_unified_cache.clear()
        self._trace_source_cache.clear()
        self._spatial_index_cache.clear()
        self._graph_cache.clear()
        self._canonical_map.clear()

    def _reset_state(self):
        self.points.clear()
        self.points_info.clear()
        self._hover_info = None
        self._clear_rb()
        self.snap_marker.hide()

    def _clear_rb(self):
        self.rb.reset(QgsWkbTypes.LineGeometry)
        self.rbGhost.reset(QgsWkbTypes.LineGeometry)

    # setup
    def _prepare_snap_and_trace_layers(self):
        self.snap_layers = []
        self.trace_layers = []
        self.boundary_layer = None
        self.boundary_unified = None
        self.other_poly_boundaries = []
        self._canonical_map.clear()

        root = QgsProject.instance().layerTreeRoot()

        # target polygon: boundary & unify -> canonical
        if isinstance(self.target_polygon_layer, QgsVectorLayer) and self.target_polygon_layer.isValid():
            self.snap_layers.append(self.target_polygon_layer)
            self.boundary_layer = self._build_boundary_lines(self.target_polygon_layer, self.selected_only)
            self.boundary_unified = self._unify_lines(self.boundary_layer) if self.boundary_layer else None

            for lyr in (self.boundary_unified, self.boundary_layer):
                if lyr and lyr.isValid() and lyr.featureCount() > 0:
                    QgsProject.instance().addMapLayer(lyr, False)  # add ẩn
                    self._temp_layers.append(lyr)

            canonical = self.boundary_unified or self.boundary_layer
            if canonical:
                # map polygon gốc + boundary liên quan -> canonical
                self._canonical_map[self.target_polygon_layer.id()] = canonical
                if self.boundary_layer:
                    self._canonical_map[self.boundary_layer.id()] = canonical
                if self.boundary_unified:
                    self._canonical_map[self.boundary_unified.id()] = canonical

                self.trace_layers.append(canonical)
                self.snap_layers.append(canonical)

        # line đang hiển thị -> canonical = unified(line) or line
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and lyr.geometryType() == QgsWkbTypes.LineGeometry:
                node = root.findLayer(lyr.id())
                vis = node.isVisible() if node else True
                if not vis:
                    continue
                uni = self._get_unified_for_layer(lyr) or lyr
                self._canonical_map[lyr.id()] = uni
                self._canonical_map[uni.id()] = uni
                self.snap_layers.append(uni)
                if uni not in self.trace_layers:
                    self.trace_layers.append(uni)

        # boundary cho các polygon khác đang bật -> canonical
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr is self.target_polygon_layer:
                continue
            if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
                node = root.findLayer(lyr.id())
                vis = node.isVisible() if node else True
                if not vis:
                    continue
                bnd = self._build_boundary_lines(lyr, selected_only=False)
                uni = self._unify_lines(bnd) if bnd else None
                canonical = uni or bnd
                for l2 in (uni, bnd):
                    if l2 and l2.isValid() and l2.featureCount() > 0:
                        QgsProject.instance().addMapLayer(l2, False)
                        self._temp_layers.append(l2)
                if canonical:
                    self._canonical_map[lyr.id()] = canonical
                    if bnd:
                        self._canonical_map[bnd.id()] = canonical
                    if uni:
                        self._canonical_map[uni.id()] = canonical
                    self.other_poly_boundaries.append(canonical)
                    self.snap_layers.append(canonical)
                    if canonical not in self.trace_layers:
                        self.trace_layers.append(canonical)

    def _build_boundary_lines(self, poly_layer, selected_only=False):
        try:
            input_layer = poly_layer
            if selected_only and poly_layer.selectedFeatureCount() > 0:
                crs = poly_layer.crs().authid()
                geom_str = 'MultiPolygon' if QgsWkbTypes.isMultiType(poly_layer.wkbType()) else 'Polygon'
                mem = QgsVectorLayer(f"{geom_str}?crs={crs}", "tmp_poly_selected", "memory")
                pr = mem.dataProvider()
                pr.addAttributes([_mk_field("id", QVariant.LongLong)])
                mem.updateFields()
                feats = []
                for f in poly_layer.getSelectedFeatures():
                    nf = QgsFeature(mem.fields()); nf.setGeometry(f.geometry()); nf["id"] = f.id(); feats.append(nf)
                if feats:
                    pr.addFeatures(feats); mem.updateExtents(); input_layer = mem

            res = processing.run('native:polygonstolines',
                                 {'INPUT': input_layer, 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                                 is_child_algorithm=True)
            out_obj = res.get('OUTPUT')
            out = out_obj if isinstance(out_obj, QgsVectorLayer) else QgsVectorLayer(out_obj, 'tmp_boundary', 'ogr')
            if not out or not out.isValid() or out.geometryType() != QgsWkbTypes.LineGeometry:
                return None
            return out
        except Exception:
            return None

    def _unify_lines(self, line_layer):
        if not line_layer or not line_layer.isValid():
            return None
        try:
            res1 = processing.run(
                'native:dissolve',
                {'INPUT': line_layer, 'FIELD': [], 'SEPARATE_DISJOINT': False,
                 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                is_child_algorithm=True)
            dis = res1.get('OUTPUT')
            dis = dis if isinstance(dis, QgsVectorLayer) else QgsVectorLayer(dis, 'tmp_dis', 'ogr')
            if not dis or not dis.isValid():
                return None
            res2 = processing.run(
                'native:linestomerge',
                {'INPUT': dis, 'FIELD': [], 'OUTPUT': QgsProcessing.TEMPORARY_OUTPUT},
                is_child_algorithm=True)
            mg = res2.get('OUTPUT')
            mg = mg if isinstance(mg, QgsVectorLayer) else QgsVectorLayer(mg, 'tmp_merge', 'ogr')
            if not mg or not mg.isValid():
                return None
            return mg
        except Exception:
            return None

    def _get_unified_for_layer(self, lyr: QgsVectorLayer):
        if not lyr or not lyr.isValid():
            return None
        lid = lyr.id()
        if lid in self._layer_unified_cache:
            un = self._layer_unified_cache[lid]
            if un and un.isValid():
                return un
        un = self._unify_lines(lyr)
        if un:
            self._layer_unified_cache[lid] = un
            self._temp_layers.append(un)
            QgsProject.instance().addMapLayer(un, False)
        return un

    def _trace_layer_for_snap_layer(self, lyr: QgsVectorLayer):
        """Trả canonical trace layer cho bất kỳ layer nào được snap vào."""
        if lyr is None or not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
            return None
        if lyr.id() in self._canonical_map:
            return self._canonical_map[lyr.id()]

        # nếu là line: canonical = unified(line) or line
        if lyr.geometryType() == QgsWkbTypes.LineGeometry:
            uni = self._get_unified_for_layer(lyr) or lyr
            self._canonical_map[lyr.id()] = uni
            self._canonical_map[uni.id()] = uni
            if uni not in self.trace_layers:
                self.trace_layers.append(uni)
            return uni

        # nếu là polygon: tạo boundary & unified rồi map
        if lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
            bnd = self._build_boundary_lines(lyr, selected_only=False)
            uni = self._unify_lines(bnd) if bnd else None
            canonical = uni or bnd
            if canonical:
                self._canonical_map[lyr.id()] = canonical
                if bnd: self._canonical_map[bnd.id()] = canonical
                if uni: self._canonical_map[uni.id()] = canonical
                QgsProject.instance().addMapLayer(canonical, False)
                self._temp_layers.append(canonical)
                if canonical not in self.trace_layers:
                    self.trace_layers.append(canonical)
                return canonical

        return None

    def _spatial_index_for(self, line_layer: QgsVectorLayer):
        if not line_layer or not line_layer.isValid():
            return None
        lid = line_layer.id()
        idx = self._spatial_index_cache.get(lid)
        if idx:
            return idx
        try:
            idx = QgsSpatialIndex(line_layer.getFeatures())
            self._spatial_index_cache[lid] = idx
            return idx
        except Exception:
            return None

    def _setup_canvas_snapping(self):
        # Đọc cấu hình snapping của Project/QGIS; plugin không ghi đè
        self._using_canvas_snapping = self.enable_snapping

    def _init_tracer(self):
        self.tracer = None
        self._have_qgstracer = False
        QgsTracerClass = None
        try:
            from qgis.analysis import QgsTracer as _QgsTracer
            QgsTracerClass = _QgsTracer
        except Exception:
            try:
                from qgis.core import QgsTracer as _QgsTracerCore
                QgsTracerClass = _QgsTracerCore
            except Exception:
                QgsTracerClass = None

        if QgsTracerClass is None:
            return

        try:
            self.tracer = QgsTracerClass()
            self._have_qgstracer = True
            ms = self.canvas().mapSettings()
            if hasattr(self.tracer, 'setMapSettings'):
                self.tracer.setMapSettings(ms)
            if hasattr(self.tracer, 'setDestinationCrs'):
                self.tracer.setDestinationCrs(ms.destinationCrs(), QgsProject.instance().transformContext())
            if hasattr(self.tracer, 'setExtent'):
                self.tracer.setExtent(ms.extent())

            # dùng **canonical layers** cho tracer
            canon_layers = list({id(v): v for v in self._canonical_map.values()}.values())
            if hasattr(self.tracer, 'setLayers'):
                self.tracer.setLayers([l for l in canon_layers if isinstance(l, QgsVectorLayer)])

            tol_map = ms.mapUnitsPerPixel() * max(2, int(self.snap_px * 4))
            if hasattr(self.tracer, 'setTolerance'):
                self.tracer.setTolerance(tol_map)
            if hasattr(self.tracer, 'setTopologyTolerance'):
                try:
                    self.tracer.setTopologyTolerance(tol_map)
                except Exception:
                    pass
            if hasattr(self.tracer, 'setAddPointsOnIntersectionsEnabled'):
                self.tracer.setAddPointsOnIntersectionsEnabled(True)
            if hasattr(self.tracer, 'rebuildGraph'):
                self.tracer.rebuildGraph()
        except Exception:
            self.tracer = None
            self._have_qgstracer = False

    # ---------- helpers snap ----------
    def _snap_with_locator(self, map_point_xy: QgsPointXY, lyr: QgsVectorLayer):
        try:
            ms = self.canvas().mapSettings()
            tol_map = QgsTolerance.toleranceInMapUnits(self.snap_px, lyr, ms)
            to_layer = QgsCoordinateTransform(ms.destinationCrs(), lyr.crs(), QgsProject.instance())
            pt_layer = to_layer.transform(map_point_xy)

            locator = QgsPointLocator(lyr, lyr.extent(), lyr.crs(), QgsProject.instance().transformContext())
            mv = locator.nearestVertex(pt_layer, tol_map)
            me = locator.nearestEdge(pt_layer, tol_map)

            best = None
            if mv.isValid():
                best = (mv.point(), mv.distance(), mv.featureId())
            if me.isValid():
                cand = (me.point(), me.distance(), me.featureId())
                if best is None or cand[1] < best[1]:
                    best = cand
            if not best:
                return None

            to_map = QgsCoordinateTransform(lyr.crs(), ms.destinationCrs(), QgsProject.instance())
            pt_map = to_map.transform(best[0])
            return QgsPointXY(pt_map), best[1], int(best[2])
        except Exception:
            return None

    def _snap_to_layers(self, map_point_xy: QgsPointXY, layers):
        if not self.enable_snapping or not layers:
            return QgsPointXY(map_point_xy), False, None, None

        best = None  # (dist, pt, lyr, fid)
        for lyr in layers:
            if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
                continue
            found = self._snap_with_locator(map_point_xy, lyr)
            if not found:
                continue
            pt, dist, fid = found
            if (best is None) or (dist < best[0]):
                best = (dist, pt, lyr, fid)

        if best:
            return best[1], True, best[2], best[3]
        return QgsPointXY(map_point_xy), False, None, None

    def _snap_via_canvas(self, map_point_xy: QgsPointXY):
        try:
            match = self.canvas().snappingUtils().snapToMap(map_point_xy)
            if match and match.isValid():
                try:
                    p = match.point()
                except Exception:
                    p = match.pointV2()
                pt = QgsPointXY(p)
                lyr = None
                fid = None
                try:
                    lyr = match.layer()
                except Exception:
                    pass
                try:
                    fid = int(match.featureId())
                except Exception:
                    fid = None
                return pt, True, lyr, fid
        except Exception:
            pass
        return QgsPointXY(map_point_xy), False, None, None

    def _snap_to_map(self, map_point_xy: QgsPointXY, prefer_trace=False):
        if prefer_trace:
            pref = [l for l in [self.boundary_unified, self.boundary_layer] if l]
            pt, ok, lyr, fid = self._snap_to_layers(map_point_xy, pref or self.trace_layers)
            if ok:
                return pt, True, lyr, fid

        if self._using_canvas_snapping:
            pt, ok, lyr, fid = self._snap_via_canvas(map_point_xy)
            if ok:
                return pt, True, lyr, fid

        return self._snap_to_layers(map_point_xy, self.snap_layers)

    def _ensure_on_layer(self, pt_map: QgsPointXY, layer: QgsVectorLayer):
        if not layer:
            return pt_map
        p2, ok, _, _ = self._snap_to_layers(pt_map, [layer])
        return p2

    # ---------- chuẩn hoá polyline ----------
    def _polyline_clean(self, pts_obj):
        flat = []

        def _it(obj):
            if obj is None:
                return
            if isinstance(obj, QgsGeometry):
                if obj.isEmpty():
                    return
                try:
                    pl = obj.asPolyline()
                    if pl:
                        for p in pl:
                            yield p
                        return
                    mpl = obj.asMultiPolyline()
                    if mpl and len(mpl) > 0:
                        for p in mpl[0]:
                            yield p
                        return
                except Exception:
                    pass
            if isinstance(obj, (list, tuple)):
                for it in obj:
                    yield from _it(it)
                return
            try:
                yield QgsPointXY(obj)
                return
            except Exception:
                pass
            try:
                yield QgsPointXY(obj.x(), obj.y())
                return
            except Exception:
                pass

        for p in _it(pts_obj):
            try:
                q = QgsPointXY(p)
            except Exception:
                continue
            if not flat or (q.x() != flat[-1].x() or q.y() != flat[-1].y()):
                flat.append(q)

        return flat if len(flat) >= 2 else None

    def _path_to_points(self, path):
        try:
            pts = path.points() if hasattr(path, 'points') else list(path)
        except Exception:
            pts = path
        return self._polyline_clean(pts)

    # ---------- danh sách canonical + đo khoảng cách ----------
    def _all_canonical_layers(self):
        return list({
            id(v): v
            for v in self._canonical_map.values()
            if isinstance(v, QgsVectorLayer)
            and v.isValid()
            and v.geometryType() == QgsWkbTypes.LineGeometry
        }.values())

    def _dist_to_layer(self, layer: QgsVectorLayer, pt_map: QgsPointXY):
        if not layer or not layer.isValid():
            return float('inf')
        try:
            ms = self.canvas().mapSettings()
            to_layer = QgsCoordinateTransform(ms.destinationCrs(), layer.crs(), QgsProject.instance())
            p = to_layer.transform(pt_map)
            tol = QgsTolerance.toleranceInMapUnits(self.snap_px * 4, layer, ms)
            loc = QgsPointLocator(layer, layer.extent(), layer.crs(), QgsProject.instance().transformContext())
            mv = loc.nearestVertex(p, tol)
            me = loc.nearestEdge(p, tol)
            best = None
            if mv.isValid():
                best = mv.distance()
            if me.isValid():
                best = me.distance() if best is None else min(best, me.distance())
            return best if best is not None else float('inf')
        except Exception:
            return float('inf')

    def _best_common_trace_layer(self, p1_map: QgsPointXY, p2_map: QgsPointXY):
        best = None
        for lyr in self._all_canonical_layers():
            d1 = self._dist_to_layer(lyr, p1_map)
            d2 = self._dist_to_layer(lyr, p2_map)
            if math.isfinite(d1) and math.isfinite(d2):
                score = d1 + d2
                if best is None or score < best[0]:
                    best = (score, lyr)
        return best[1] if best else None

    # ---------- ĐỒ THỊ (GRAPH) ----------
    def _graph_for_layer(self, layer: QgsVectorLayer):
        if not layer or not layer.isValid():
            return None
        lid = layer.id()
        g = self._graph_cache.get(lid)
        if g:
            return g

        ms = self.canvas().mapSettings()
        to_map = QgsCoordinateTransform(layer.crs(), ms.destinationCrs(), QgsProject.instance())

        nodes, key2id, adj = [], {}, {}

        def key_of(pt):
            return (round(pt.x(), 8), round(pt.y(), 8))

        def add_node(pt):
            k = key_of(pt)
            idx = key2id.get(k)
            if idx is None:
                idx = len(nodes)
                nodes.append(pt)
                key2id[k] = idx
                adj[idx] = []
            return idx

        def add_edge(i, j):
            if i == j:
                return
            pi = nodes[i]; pj = nodes[j]
            w = math.hypot(pj.x() - pi.x(), pj.y() - pi.y())
            adj[i].append((j, w)); adj[j].append((i, w))

        for f in layer.getFeatures():
            gtry = f.geometry()
            if not gtry or gtry.isEmpty():
                continue
            try:
                poly = gtry.asPolyline()
                if poly and len(poly) >= 2:
                    pts = [to_map.transform(QgsPointXY(p)) for p in poly]
                    last = None
                    for p in pts:
                        pid = add_node(p)
                        if last is not None:
                            add_edge(last, pid)
                        last = pid
                    continue
                mpl = gtry.asMultiPolyline()
                if mpl and len(mpl) > 0:
                    for one in mpl:
                        if len(one) < 2:
                            continue
                        pts = [to_map.transform(QgsPointXY(p)) for p in one]
                        last = None
                        for p in pts:
                            pid = add_node(p)
                            if last is not None:
                                add_edge(last, pid)
                            last = pid
            except Exception:
                continue

        graph = dict(nodes=nodes, key2id=key2id, adj=adj)
        self._graph_cache[lid] = graph
        return graph

    def _nearest_node_on_graph(self, layer: QgsVectorLayer, graph, pt_map: QgsPointXY):
        if not graph or not layer or not layer.isValid():
            return None
        ms = self.canvas().mapSettings()
        to_layer = QgsCoordinateTransform(ms.destinationCrs(), layer.crs(), QgsProject.instance())
        p_layer = to_layer.transform(pt_map)
        try:
            locator = QgsPointLocator(layer, layer.extent(), layer.crs(), QgsProject.instance().transformContext())
            mv = locator.nearestVertex(p_layer, QgsTolerance.toleranceInMapUnits(self.snap_px * 2, layer, ms))
            if not mv.isValid():
                return None
            to_map = QgsCoordinateTransform(layer.crs(), ms.destinationCrs(), QgsProject.instance())
            p_map = to_map.transform(mv.point())
            k = (round(p_map.x(), 8), round(p_map.y(), 8))
            nid = graph['key2id'].get(k)
            if nid is not None:
                return nid
            best = None
            bx = p_map.x(); by = p_map.y()
            for i, q in enumerate(graph['nodes']):
                d2 = (q.x() - bx) ** 2 + (q.y() - by) ** 2
                if best is None or d2 < best[0]:
                    best = (d2, i)
            return best[1] if best else None
        except Exception:
            return None

    def _dijkstra(self, adj, start, goal):
        if start is None or goal is None:
            return None
        INF = 10**30
        dist, prev = {}, {}
        pq = []
        dist[start] = 0.0
        heapq.heappush(pq, (0.0, start))
        while pq:
            d, u = heapq.heappop(pq)
            if u == goal:
                break
            if d > dist.get(u, INF):
                continue
            for v, w in adj.get(u, []):
                nd = d + w
                if nd < dist.get(v, INF):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if goal not in dist:
            return None
        path = [goal]
        cur = goal
        while cur != start:
            cur = prev[cur]
            path.append(cur)
        path.reverse()
        return path

    # ---------- substring/trace ----------
    def _subline_on_layer_any_feature(self, layer: QgsVectorLayer, p1_map: QgsPointXY, p2_map: QgsPointXY):
        if not layer or not layer.isValid():
            return None
        unified = layer  # canonical đã là line; có thể là unified
        try:
            ms = self.canvas().mapSettings()
            to_layer = QgsCoordinateTransform(ms.destinationCrs(), unified.crs(), QgsProject.instance())
            p1 = to_layer.transform(p1_map); p2 = to_layer.transform(p2_map)

            idx = self._spatial_index_for(unified)
            cand_ids = set()
            if idx:
                try: cand_ids.update(idx.nearestNeighbor(p1, 16))
                except Exception: pass
                try: cand_ids.update(idx.nearestNeighbor(p2, 16))
                except Exception: pass
            feats = (unified.getFeatures(QgsFeatureRequest().setFilterFids(list(cand_ids)))
                     if cand_ids else unified.getFeatures())

            tol = QgsTolerance.toleranceInMapUnits(self.snap_px * 3, unified, ms)
            pt1g = QgsGeometry.fromPointXY(p1); pt2g = QgsGeometry.fromPointXY(p2)

            for f in feats:
                g = f.geometry()
                if not g or g.isEmpty():
                    continue
                if g.distance(pt1g) > tol or g.distance(pt2g) > tol:
                    continue
                d1 = g.lineLocatePoint(pt1g)
                d2 = g.lineLocatePoint(pt2g)
                if d1 < 0 or d2 < 0:
                    continue
                lo, hi = (d1, d2) if d1 <= d2 else (d2, d1)
                sub = g.lineSubstring(lo, hi)
                if not sub or sub.isEmpty():
                    continue

                # đổi về CRS map
                sub_map = QgsGeometry(sub)
                xform = QgsCoordinateTransform(unified.crs(), ms.destinationCrs(), QgsProject.instance())
                try:
                    sub_map.transform(xform)
                except Exception:
                    return None
                return sub_map
        except Exception:
            return None
        return None

    def _trace_with_specific_layer(self, layer: QgsVectorLayer, p1_map: QgsPointXY, p2_map: QgsPointXY):
        if not self._have_qgstracer or self.tracer is None or not layer or not layer.isValid():
            return None
        try:
            old_layers = None
            if hasattr(self.tracer, 'layers'):
                try:
                    old_layers = self.tracer.layers()
                except Exception:
                    old_layers = None
            if hasattr(self.tracer, 'setLayers'):
                self.tracer.setLayers([layer])
            if hasattr(self.tracer, 'rebuildGraph'):
                self.tracer.rebuildGraph()

            p1 = self._ensure_on_layer(p1_map, layer)
            p2 = self._ensure_on_layer(p2_map, layer)
            raw = None
            try:
                raw = self.tracer.findShortestPath(p1, p2) if hasattr(self.tracer, 'findShortestPath') else self.tracer.findPath(p1, p2)
            except Exception:
                raw = None
            pts = self._path_to_points(raw)

            if old_layers is not None and hasattr(self.tracer, 'setLayers'):
                try:
                    self.tracer.setLayers(old_layers)
                    if hasattr(self.tracer, 'rebuildGraph'):
                        self.tracer.rebuildGraph()
                except Exception:
                    pass
            return pts
        except Exception:
            return None

    def _graph_path_on_layer(self, layer: QgsVectorLayer, p1_map: QgsPointXY, p2_map: QgsPointXY):
        g = self._graph_for_layer(layer)
        if not g:
            return None
        n1 = self._nearest_node_on_graph(layer, g, p1_map)
        n2 = self._nearest_node_on_graph(layer, g, p2_map)
        if n1 is None or n2 is None:
            return None
        path_nodes = self._dijkstra(g['adj'], n1, n2)
        if not path_nodes:
            return None
        pts = [g['nodes'][i] for i in path_nodes]
        if pts and (pts[0].x() != p1_map.x() or pts[0].y() != p1_map.y()):
            pts = [p1_map] + pts
        if pts and (pts[-1].x() != p2_map.x() or pts[-1].y() != p2_map.y()):
            pts = pts + [p2_map]
        return pts if len(pts) >= 2 else None

    def _traced_path_between(self, last_info, cur_info):
        if not last_info or not cur_info:
            return None

        tl1 = last_info.get('trace_layer')
        tl2 = cur_info.get('trace_layer')

        # Ưu tiên: nếu cùng 1 canonical layer → đi theo lớp đó
        layer = tl1 if (tl1 is not None and tl1 == tl2) else None

        # Nếu khác lớp hoặc thiếu lớp → chọn lớp chung tốt nhất (theo khoảng cách)
        if layer is None:
            layer = self._best_common_trace_layer(last_info['pt'], cur_info['pt'])

        # Nếu đã có 1 lớp line phù hợp → thử theo thứ tự: substring → tracer → đồ thị
        if layer is not None:
            sub2 = self._subline_on_layer_any_feature(layer, last_info['pt'], cur_info['pt'])
            if sub2 and not sub2.isEmpty():
                return self._polyline_clean(sub2)

            pts = self._trace_with_specific_layer(layer, last_info['pt'], cur_info['pt'])
            if pts:
                return pts

            pts = self._graph_path_on_layer(layer, last_info['pt'], cur_info['pt'])
            if pts:
                return pts

        # Cuối cùng: thử tracer chung (ít khi cần)
        if self.tracer is not None:
            p1 = self._ensure_on_layer(last_info['pt'], tl1)
            p2 = self._ensure_on_layer(cur_info['pt'], tl2)
            raw = None
            try:
                raw = self.tracer.findShortestPath(p1, p2) if hasattr(self.tracer, 'findShortestPath') else self.tracer.findPath(p1, p2)
            except Exception:
                raw = None
            pts = self._path_to_points(raw)
            if pts:
                return pts

        return None

    def _update_ghost(self):
        self.rbGhost.reset(QgsWkbTypes.LineGeometry)
        if len(self.points_info) == 0 or not self._hover_info:
            return
        last_info = self.points_info[-1]
        cur_info = self._hover_info
        if self.enable_tracing:
            pts = self._traced_path_between(last_info, cur_info)
            if pts:
                self.rbGhost.setToGeometry(QgsGeometry.fromPolylineXY(pts), None)
                return
        self.rbGhost.setToGeometry(QgsGeometry.fromPolylineXY([last_info['pt'], cur_info['pt']]), None)

    # events
    def canvasMoveEvent(self, e):
        mp = e.mapPoint()
        pt, snapped, lyr, fid = self._snap_to_map(QgsPointXY(mp), prefer_trace=self.enable_tracing)
        trace_lyr = self._trace_layer_for_snap_layer(lyr)
        self._hover_info = dict(pt=pt, snap_layer=lyr, snap_fid=fid if snapped else None, trace_layer=trace_lyr)
        if snapped:
            self.snap_marker.setCenter(pt)
            self.snap_marker.show()
        else:
            self.snap_marker.hide()
        self._update_ghost()

    def canvasPressEvent(self, e):
        if e.button() == Qt.LeftButton:
            mp = e.mapPoint()
            pt, snapped, lyr, fid = self._snap_to_map(QgsPointXY(mp), prefer_trace=self.enable_tracing)
            trace_lyr = self._trace_layer_for_snap_layer(lyr)
            cur_info = dict(pt=pt, snap_layer=lyr, snap_fid=fid if snapped else None, trace_layer=trace_lyr)

            if self.enable_tracing and len(self.points_info) >= 1:
                last_info = self.points_info[-1]
                pts = self._traced_path_between(last_info, cur_info)
                if pts:
                    for i, q in enumerate(pts):
                        if i == 0:
                            continue
                        self.points.append(q)
                        self.points_info.append(dict(pt=q, snap_layer=lyr, snap_fid=cur_info['snap_fid'], trace_layer=trace_lyr))
                        self.rb.addPoint(q, True)
                    self._hover_info = cur_info
                    self._update_ghost()
                    return

            self.points.append(pt)
            self.points_info.append(cur_info)
            self.rb.addPoint(pt, True)
            self._hover_info = cur_info
            self._update_ghost()

        elif e.button() == Qt.RightButton:
            if len(self.points) >= 2:
                self.on_finish(QgsGeometry.fromPolylineXY(self.points))
            self._reset_state()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._reset_state()
            return
        if e.key() == Qt.Key_Backspace:
            if self.points:
                self.points.pop()
            if self.points_info:
                self.points_info.pop()
            try:
                self.rb.removeLastPoint()
            except Exception:
                self.rb.reset(QgsWkbTypes.LineGeometry)
                for p in self.points:
                    self.rb.addPoint(QgsPointXY(p), True)
            cur = self.canvas().mouseLastXY()
            pt_map = self.canvas().getCoordinateTransform().toMapCoordinates(cur.x(), cur.y())
            pt, snapped, lyr, fid = self._snap_to_map(QgsPointXY(pt_map), prefer_trace=self.enable_tracing)
            trace_lyr = self._trace_layer_for_snap_layer(lyr)
            self._hover_info = dict(pt=pt, snap_layer=lyr, snap_fid=fid if snapped else None, trace_layer=trace_lyr)
            self._update_ghost()


# ---------------- Controller (tạo Dock, start/stop/apply, gọi thuật toán) ----------------
class SplitDrawController:
    def __init__(self, iface):
        self.iface = iface
        self.tool = None
        self.conf = None
        self.dock = None
        self.ui = None

    def _ensure_dock(self):
        if self.dock and self.ui:
            return
        self.ui = SplitConfigWidget(self.iface)
        self.dock = QtWidgets.QDockWidget("Vẽ để chia tách vùng — Lâm nghiệp 4.0", self.iface.mainWindow())
        self.dock.setObjectName("ForestrySplitDock")
        self.dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.dock.setWidget(self.ui)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)

        # wire signals
        self.ui.startRequested.connect(self._on_start)
        self.ui.stopRequested.connect(self._on_stop)
        self.ui.applyRequested.connect(self._on_apply)

    def toggle(self):
        # Mở/hiện Dock để người dùng luôn thấy cấu hình
        self._ensure_dock()
        self.dock.show()
        self.ui.raise_()
        self.ui.setStatus("Chưa bắt đầu vẽ", good=False)

    # ---- actions ----
    def _on_start(self):
        lyr = self.ui.layer()
        if not lyr:
            QtWidgets.QMessageBox.warning(self.iface.mainWindow(), "Thiếu lớp", "Không có lớp polygon nào.")
            return

        self.conf = dict(
            layer=lyr,
            value_field=self.ui.value_field(),
            area_field=self.ui.area_field(),
            opts=self.ui.options()
        )

        self.tool = DrawLineTool(self.iface, self._finish, self.conf['opts'], self.conf['layer'])
        self.iface.mapCanvas().setMapTool(self.tool)

        hint = "Nhấp trái để vẽ; nhấp phải để kết thúc; Esc để huỷ. Backspace: xoá đỉnh cuối."
        if self.conf['opts'].get('tracing', False):
            hint += " Tracing: nếu hai điểm cùng một lớp (kể cả polygon), đường sẽ ép theo chính lớp đó."
        self.iface.messageBar().pushInfo("Chia tách", hint)
        self.ui.setStatus(f"Đang vẽ trên lớp: <b>{lyr.name()}</b>", good=True)

    def _on_stop(self):
        if self.tool:
            self.iface.mapCanvas().unsetMapTool(self.tool)
            self.tool = None
        self.ui.setStatus("Đã dừng vẽ", good=False)

    def _on_apply(self):
        if not self.tool:
            self._on_start()
            return
        # cập nhật tool hiện tại theo cấu hình mới
        self.conf['layer'] = self.ui.layer()
        self.conf['value_field'] = self.ui.value_field()
        self.conf['area_field'] = self.ui.area_field()
        self.conf['opts'] = self.ui.options()
        self.tool.reload(self.conf['layer'], self.conf['opts'])
        self.ui.setStatus(f"Đang vẽ trên lớp: <b>{self.conf['layer'].name()}</b> (đã áp dụng cấu hình)", good=True)

    def _finish(self, line_in_canvas_crs):
        lyr = self.conf['layer']
        dst_crs: QgsCoordinateReferenceSystem = lyr.crs()
        src_crs: QgsCoordinateReferenceSystem = self.iface.mapCanvas().mapSettings().destinationCrs()
        geom = QgsGeometry(line_in_canvas_crs)

        if src_crs != dst_crs:
            xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
            try:
                geom.transform(xform)
            except Exception:
                pass

        # tạo line memory
        line_lyr = QgsVectorLayer(f'LineString?crs={dst_crs.authid()}', 'split_draw_line_tmp', 'memory')
        pr = line_lyr.dataProvider()
        pr.addAttributes([_mk_field('id', QVariant.Int)])
        line_lyr.updateFields()
        f = QgsFeature(line_lyr.fields()); f.setGeometry(geom); f['id'] = 1
        pr.addFeature(f); line_lyr.updateExtents()

        params = {
            'INPUT': lyr,
            'LINES': line_lyr,
            'SELECTED_ONLY': self.conf['opts'].get('selected_only', False),
            'PRESERVE': self.conf['opts'].get('preserve', False),
            'VALUE_FIELD': self.conf['value_field'],
            'RECALC_AREA': self.conf['opts'].get('recalc_area', False),
            'AREA_FIELD': self.conf['area_field'],
            'AREA_UNITS_MODE': self.conf['opts'].get('area_units_mode', 0),
        }

        processing.run(ALG_ID, params)
        # giữ tool để tiếp tục vẽ tiếp
        self.ui.setStatus(f"Đã tách xong trên lớp: <b>{lyr.name()}</b>. Bạn có thể tiếp tục vẽ.", good=True)

# ---- Compatibility shim for old imports ----
# Some older code may still import MinimalSplitConfig.
# We wrap SplitConfigWidget inside a simple QDialog to keep the same API.
class MinimalSplitConfig(QtWidgets.QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chia tách (vẽ đường) — Cấu hình")
        self.setMinimumWidth(560)
        # embed the new widget
        self._ui = SplitConfigWidget(iface, self)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self._ui)
        # OK/Cancel buttons like the old dialog
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, parent=self
        )
        lay.addWidget(btns)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

    # keep same API as the old dialog
    def layer(self):
        return self._ui.layer()

    def value_field(self):
        return self._ui.value_field()

    def area_field(self):
        return self._ui.area_field()

    def options(self):
        return self._ui.options()
