# -*- coding: utf-8 -*-
def classFactory(iface):
    from .plugin_main import ForestryToolPlugin
    return ForestryToolPlugin(iface)
