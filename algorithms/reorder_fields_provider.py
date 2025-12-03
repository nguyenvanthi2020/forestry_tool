# -*- coding: utf-8 -*-
from qgis.core import QgsProcessingProvider
from .reorder_fields_algorithm import ReorderFieldsAlgorithm

class ReorderFieldsProvider(QgsProcessingProvider):
    def id(self):
        return "reorder_fields_provider"

    def name(self):
        return "Reorder/Cast Fields"

    def longName(self):
        return "Reorder & Cast Shapefile Fields (Fixed Schema)"

    def loadAlgorithms(self):
        self.addAlgorithm(ReorderFieldsAlgorithm())
