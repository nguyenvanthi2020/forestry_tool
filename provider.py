from qgis.core import QgsProcessingProvider
from .algorithms.dien_so_hieu_lo import DienSoHieuLoAlg
from .algorithms.reorder_fields_algorithm import ReorderFieldsAlgorithm
from .algorithms.assign_codes_algorithm_tt33 import AssignCodesAlgorithm33
from .algorithms.assign_from_maldlr_algorithm_tt33 import AssignFromMaldlrAlgorithm33
from .algorithms.join_from_json_by_maxa import JoinFromJsonByMaxa
from .algorithms.font_converter_algorithm import VNEncodingConvertAlgorithm
from .algorithms.aggregate_with_filter import AggregateWithFilter
from .algorithms.aggregate_with_filter_ui import AggregateWithFilterUI
from .algorithms.VNStripDiacriticsAlgorithm import VNStripDiacriticsAlgorithm
from .algorithms.merge_validated_vectors import MergeValidatedVectors
from .algorithms.split_by_field_condition import SplitByFieldConditionAlgorithm
from .algorithms.raster_outlier_filter import RasterOutlierFilterFast
from .algorithms.raster_outlier_filter_single import RasterOutlierFilterSingle
from .algorithms.stream_network_from_dem import StreamFromDEM
from .algorithms.watershed_algorithm import WatershedFromDEM
from .algorithms.convert_to_dgn import ExportToDGNWithLabelsAlgorithm
from .algorithms.convert_dgn_to_shp import DGNToSHP_WithText
from .algorithms.smart_spliter import SplitFeaturesPreserveAlgorithm
from .algorithms.split_inplace_algorithm import SplitPolygonsInPlaceAlgorithm
from .algorithms.multilayers_schema_compare import AlignFieldsToReference
from .algorithms.download_era5_generic import DownloadERA5GenericAlgorithm
from .algorithms.earthdata_dem_algorithm import EarthdataDemAlgorithm
from .algorithms.gee_dem_download import GEEDemDownloadAlg
from . import resources
class ForestryToolPluginProvider(QgsProcessingProvider):
    def id(self):
        return "forestry_tool"
    def icon(self):
        from qgis.PyQt.QtGui import QIcon
        return QIcon(":/plugins/forestry_tool/icons/plugin.png")
    def name(self):
        return "Lâm nghiệp 4.0"

    def loadAlgorithms(self):
        self.addAlgorithm(DienSoHieuLoAlg())
        self.addAlgorithm(ReorderFieldsAlgorithm())
        self.addAlgorithm(AssignCodesAlgorithm33())
        self.addAlgorithm(AssignFromMaldlrAlgorithm33())
        self.addAlgorithm(JoinFromJsonByMaxa())
        self.addAlgorithm(VNEncodingConvertAlgorithm())
        self.addAlgorithm(AggregateWithFilter())
        self.addAlgorithm(AggregateWithFilterUI())
        self.addAlgorithm(VNStripDiacriticsAlgorithm())
        self.addAlgorithm(MergeValidatedVectors())
        self.addAlgorithm(SplitByFieldConditionAlgorithm())
        self.addAlgorithm(RasterOutlierFilterFast())
        self.addAlgorithm(RasterOutlierFilterSingle())
        self.addAlgorithm(StreamFromDEM())
        self.addAlgorithm(WatershedFromDEM())
        self.addAlgorithm(ExportToDGNWithLabelsAlgorithm())
        self.addAlgorithm(DGNToSHP_WithText())
        self.addAlgorithm(SplitFeaturesPreserveAlgorithm())
        self.addAlgorithm(SplitPolygonsInPlaceAlgorithm())
        self.addAlgorithm(AlignFieldsToReference())
        self.addAlgorithm(DownloadERA5GenericAlgorithm())
        self.addAlgorithm(EarthdataDemAlgorithm())
        self.addAlgorithm(GEEDemDownloadAlg())
        
class ForestryToolPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initGui(self):
        from qgis.core import QgsApplication
        self.provider = ForestryToolPluginProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        from qgis.core import QgsApplication
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
