# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QAction, QMenu
from qgis.core import QgsApplication
from qgis import processing
from .provider import ForestryToolPluginProvider
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.utils import iface
from .algorithms.split_draw_controller import MinimalSplitConfig, SplitDrawController
from qgis.PyQt import QtWidgets, QtCore
from .algorithms.dem_dockwidget import GEEDemDockWidget
from .algorithms.nasa_dem_dockwidget import EarthdataDemDock

class ForestryToolPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.main_menu = None
        self.menu_raster = None
        self.menu_vector = None
        self.menu_vietnamese = None
        self.menu_stats = None
        self.actions = []
        self.proc_provider = None
        self.action_split_draw = None
        self.split_draw_ctrl = None
        self.dem_dock = None
        self.earthdata_dock = None
    # ---------- Khởi tạo GUI ----------
    def initGui(self):
        # 1) Đăng ký Processing Provider -> xuất hiện trong Processing Toolbox
        self.provider = ForestryToolPluginProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

        # 2) Tạo menu chính "Lâm nghiệp 4.0"
        self.main_menu = QMenu("Lâm &nghiệp 4.0", self.iface.mainWindow())
        self.iface.mainWindow().menuBar().addMenu(self.main_menu)

        # 3) Các submenu
        self.menu_raster = self.main_menu.addMenu("Tiện ích Raster")
        self.menu_vector = self.main_menu.addMenu("Tiện ích Vector")
        self.menu_field = self.main_menu.addMenu("Tiện ích trường")
        self.menu_vietnamese = self.main_menu.addMenu("Tiện ích Tiếng Việt")
        self.menu_stats = self.main_menu.addMenu("Thống kê")

        # ====== RASTER ======
        self._add_action(self.menu_raster,
                         "Xử lý điểm ảnh bất thường (đa Band)",
                         self.runRasterOutlierMulti)
        self._add_action(self.menu_raster,
                         "Xử lý điểm ảnh bất thường (đơn Band)",
                         self.runRasterOutlierSingle)
        self._add_action(self.menu_raster,
                         "Tạo mạng lưới và phân cấp sông suối từ DEM",
                         self.runRasterStreamNetWork)
        self._add_action(self.menu_raster,
                         "Khoanh vẽ ranh giới lưu vực từ DEM",
                         lambda: self._exec("forestry_tool:watershed_from_dem"))
        self._add_action(self.menu_raster,
                         "Tải dữ liệu thời tiết ERA5",
                         lambda: self._exec("forestry_tool:download_era5_generic"))
        self._add_action(self.menu_raster,
                         "Tải DEM (Google Earth Engine)",
                         self.showDemDock)
        self._add_action(self.menu_raster,
                        "Tải DEM (NASA Earthdata)",
                         self.showEarthdataDock)

        # ====== FIELD ======
        # Sắp xếp & chuẩn hóa trường (51 trường) – đổi id nếu bạn đặt khác
        self._add_action(self.menu_field,
                         "Sắp xếp và chuẩn hóa trường (51 trường)",
                         lambda: self._exec("forestry_tool:reorder_cast_mixedsizes_sink"))
        # (Ví dụ) Chuẩn hóa maldlr/nggocr theo ldlr – đổi id nếu bạn đặt khác
        self._add_action(self.menu_field,
                         "Chuẩn hóa maldlr và nggocr theo ldlr (TT33)",
                         lambda: self._exec("forestry_tool:assign_maldlr_nggocr_by_ldlr"))
        # (Ví dụ) Gán ldlr & nggocr theo maldlr – đổi id nếu bạn đặt khác
        self._add_action(self.menu_field,
                         "Chuẩn hóa ldlr và nggocr theo maldlr (TT33)",
                         lambda: self._exec("forestry_tool:assign_ldlr_nggocr_from_maldlr"))        
        self._add_action(self.menu_field,
                         "Cập nhật đơn vị hành chính 2 cấp",
                         lambda: self._exec("forestry_tool:join_from_json_by_maxa"))
        self._add_action(self.menu_field,
                         "Điền số hiệu lô",
                         lambda: self._exec("forestry_tool:dien_so_hieu_lo_text_by_vd_desc_kd_asc"))

        # ====== VECTOR ======
        # Tách lớp theo trường & điều kiện – đổi id nếu bạn đặt khác
        self._add_action(self.menu_vector,
                         "Tách lớp theo trường và điều kiện",
                         lambda: self._exec("forestry_tool:split_by_field_condition"))
        self._add_action(self.menu_vector,
                         "Ghép các lớp bản đồ (nghiêm ngặt)",
                         lambda: self._exec("forestry_tool:merge_validated_vectors"))
        self._add_action(self.menu_vector,
                         "Chuyển lớp Vector sang DGN v7",
                         lambda: self._exec("forestry_tool:export_to_dgn_with_labels"))
        self._add_action(self.menu_vector,
                         "Chuyển DGN v7 sang Shapefile",
                         lambda: self._exec("forestry_tool:dgn_to_shp_with_text"))
        self._add_action(self.menu_vector,
                         "Chia tách thông minh",
                         lambda: self._exec("forestry_tool:split_features_preserve"))

        # Tạo/khôi phục 1 toolbar duy nhất cho plugin
        self.toolbar = self.iface.mainWindow().findChild(QtWidgets.QToolBar, "ForestryToolToolbar")
        if not self.toolbar:
            self.toolbar = self.iface.addToolBar("Lâm nghiệp 4.0")
            self.toolbar.setObjectName("ForestryToolToolbar")
        # icon chia tách thông minh
        self.action_split = QAction(QIcon(':/plugins/forestry_tool/icons/cutting1.png'),
            'Chia tách thông minh', self.iface.mainWindow())
        self.action_split.setToolTip('Chia tách thông minh')
        self.action_split.triggered.connect(self.open_split_dialog)
        self.toolbar.addAction(self.action_split)

        # (giữ menu nếu bạn muốn)
        #self.iface.addPluginToMenu('Tiện ích Vector', self.action_split)

        # icon Vẽ để tách toolbar
        self.split_draw_ctrl = SplitDrawController(self.iface)
        self.action_split_draw = QtWidgets.QAction(
            QIcon(':/plugins/forestry_tool/icons/split1.png'),
            "Vẽ đường để chia tách polygon",
            self.iface.mainWindow()
        )
        self.action_split_draw.setToolTip("Bật công cụ Vẽ đường để chia tách polygon")
        self.action_split_draw.triggered.connect(self.split_draw_ctrl.toggle)

        # add vào cùng 1 toolbar
        self.toolbar.addAction(self.action_split_draw)

        # (tuỳ chọn) vẫn thêm vào menu Vector
        self.menu_vector.addAction(self.action_split_draw)


        # ====== TIẾNG VIỆT ======
        # Chuyển tiếng Việt có dấu -> không dấu – đổi id nếu bạn đặt khác
        self._add_action(self.menu_vietnamese,
                         "Bỏ dấu ký tự tiếng Việt",
                         lambda: self._exec("forestry_tool:vn_strip_diacritics"))
        # Chuyển bảng mã (nếu có) – đổi id nếu bạn đặt khác
        self._add_action(self.menu_vietnamese,
                         "Chuyển đổi bảng mã tiếng Việt",
                         lambda: self._exec("forestry_tool:vn_encoding_convert"))

        # ====== THỐNG KÊ ======
        # Thống kê có điều kiện + group by + hàm tổng hợp – đổi id nếu bạn đặt khác
        self._add_action(self.menu_stats,
                         "Thống kê có điều kiện",
                         lambda: self._exec("forestry_tool:aggregate_with_filter"))
        self._add_action(self.menu_stats,
                         "Thống kê có điều kiện (Dựng sẵn)",
                         lambda: self._exec("forestry_tool:aggregate_with_filter_ui"))

    def _add_action(self, menu: QMenu, text: str, slot):
        act = QAction(text, self.iface.mainWindow())
        act.triggered.connect(slot)
        menu.addAction(act)
        self.actions.append(act)

    # ---------- Dọn dẹp ----------
    def unload(self):
        for act in self.actions:
            try:
                act.deleteLater()
            except Exception:
                pass
        self.actions.clear()

        if self.main_menu:
            try:
                self.iface.mainWindow().menuBar().removeAction(self.main_menu.menuAction())
            except Exception:
                pass
            self.main_menu = None

        if self.provider:
            try:
                QgsApplication.processingRegistry().removeProvider(self.provider)
            except Exception:
                pass
            self.provider = None
        if self.action_split:
            iface.removeToolBarIcon(self.action_split)
            iface.removePluginMenu('Tiện ích Vector', self.action_split)

        # Gỡ "Chia tách thông minh"
        if getattr(self, 'action_split', None):
            try:
                if getattr(self, 'toolbar', None):
                    self.toolbar.removeAction(self.action_split)
                self.iface.removePluginMenu('Tiện ích Vector', self.action_split)
            except Exception:
                pass
            self.action_split.deleteLater()
            self.action_split = None

        # Gỡ "Vẽ đường…"
        if getattr(self, 'action_split_draw', None):
            try:
                if getattr(self, 'toolbar', None):
                    self.toolbar.removeAction(self.action_split_draw)
                # Không còn dùng Plugins toolbar nên không cần removeToolBarIcon ở đây
            except Exception:
                pass
            self.action_split_draw.deleteLater()
            self.action_split_draw = None

        # (tuỳ chọn) Nếu bạn muốn xoá luôn toolbar khi unload:
        if getattr(self, 'toolbar', None):
            try:
                # Chỉ xoá nếu không còn action nào của plugin
                if len(self.toolbar.actions()) == 0:
                    self.iface.mainWindow().removeToolBar(self.toolbar)
            except Exception:
                pass
            self.toolbar = None


        # Bỏ Provider nếu bạn muốn gỡ khi unload
        if self.proc_provider:
            try:
                QgsApplication.processingRegistry().removeProvider(self.proc_provider)
            except Exception:
                pass
            self.proc_provider = None

        # Gỡ bỏ dockWidget GEE
        if getattr(self, "dem_dock", None):
            try:
                self.iface.removeDockWidget(self.dem_dock)
            except Exception:
                pass
            self.dem_dock = None
        # Gỡ bỏ dockWidget Earthdata
        if getattr(self, "earthdata_dock", None):
            try:
                self.iface.removeDockWidget(self.earthdata_dock)
            except Exception:
                pass
            self.earthdata_dock = None

    # ---------- Mở dialog thuật toán ----------
    def _exec(self, alg_id: str):
        # Mở hộp thoại Processing với tham số của thuật toán
        processing.execAlgorithmDialog(alg_id)

    # Cụ thể cho Raster outlier
    def runRasterOutlierMulti(self):
        self._exec("forestry_tool:raster_outlier_filter_fast")

    def runRasterOutlierSingle(self):
        self._exec("forestry_tool:raster_outlier_filter_single")
    
    def runRasterStreamNetWork(self):
        self._exec("forestry_tool:stream_network_from_dem")

    # ==========================

    def open_split_dialog(self):
    # Mở hộp thoại chạy thuật toán theo id đăng ký
    # Nếu bạn đóng gói thuật toán này trong provider của bạn, thay id bên dưới bằng id thực tế
    # Ví dụ: 'yourprovider:split_features_preserve'
        alg_id = 'forestry_tool:split_features_preserve'
        try:
            from qgis import processing
            processing.execAlgorithmDialog(alg_id)
        except Exception as e:
            from qgis.PyQt.QtWidgets import QMessageBox
            QMessageBox.critical(iface.mainWindow(), 'Lỗi', f'Không mở được thuật toán {alg_id}:\n{e}')

    def showDemDock(self):
        if self.dem_dock is None:
            self.dem_dock = GEEDemDockWidget(self.iface)
            self.iface.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.dem_dock)
        self.dem_dock.show()
        self.dem_dock.raise_()
    def showEarthdataDock(self):
        if self.earthdata_dock is None:
            self.earthdata_dock = EarthdataDemDock(self.iface.mainWindow())
            self.iface.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.earthdata_dock)
        self.earthdata_dock.show()
        self.earthdata_dock.raise_()