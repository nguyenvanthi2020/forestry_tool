# -*- coding: utf-8 -*-
"""
Chuyển DGN/DXF sang Shapefile gộp theo kiểu hình học (POINTS / LINES / POLYGONS)

- Nhận dạng kiểu hình học THEO TỪNG FEATURE (hỗn hợp trong cùng sublayer vẫn đúng).
- Điểm có trường chữ (Text/Label/String/RefName...) ⇒ coi là text element: ghi TEXT.
- Xuất tối đa 3 file gộp: <TENFILE>_POINTS.shp, _LINES.shp, _POLYGONS.shp.
- Tuỳ chọn: TÁCH THEO LEVEL → ghi <TENFILE>_<LEVEL>_<GEOM>.shp cho từng Level.
- Tương thích QGIS ≥ 3.16 … 3.44.

Chống “File In Use” trên Windows:
- Sau khi ghi file, KHÔNG nạp shapefile vào project.
- Nếu người dùng chọn “ADD_TO_PROJECT”, thuật toán tạo bản sao **memory** và chỉ thêm bản memory vào project.
- Giải phóng mọi handle OGR/QGIS, invalidate cache và gc.collect() để không khóa file.

Lưu ý: DGNv8 cần GDAL build có driver DGNv8.
"""

import os, re, json, glob, time, unicodedata, gc
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterFile, QgsProcessingParameterCrs,
    QgsProcessingParameterFolderDestination, QgsProcessingParameterBoolean,
    QgsProcessingOutputMultipleLayers,
    QgsVectorLayer, QgsFeature, QgsFields, QgsField, QgsWkbTypes, QgsGeometry,
    QgsVectorFileWriter, QgsProject, QgsProviderRegistry, QgsFeatureRequest
)
from osgeo import ogr, osr


class DGNToSHP_WithText(QgsProcessingAlgorithm):
    PARAM_INPUT   = "INPUT"
    PARAM_CRS     = "FORCE_CRS"
    PARAM_OUTDIR  = "OUTPUT_DIR"
    PARAM_ADD     = "ADD_TO_PROJECT"
    PARAM_LIST    = "LIST_SUBLAYERS"
    PARAM_SPLITLV = "SPLIT_BY_LEVEL"
    OUT_FILES     = "OUTPUT_FILES"

    def tr(self, s): return QCoreApplication.translate("DGNToSHP_WithText", s)
    def createInstance(self): return DGNToSHP_WithText()
    def name(self): return "dgn_to_shp_with_text"
    def displayName(self): return self.tr("Chuyển DGN v7 sang Shapefile")
    def group(self): return self.tr("Tiện ích Vector")
    def groupId(self): return "vector_utils"
    def shortHelpString(self):
        return self.tr("""
        Thuật toán Chuyển DGN sang Shapefile

        Mỗi feature trong tệp CAD sẽ được phân loại theo kiểu hình học (Point, Line, Polygon).
           - Điểm có chứa trường chữ (Text, Label, String, RefName...) sẽ coi như Text và ghi thuộc tính TEXT.
           - Đầu ra gồm tối đa 3 shapefile gộp: <TENFILE>_POINTS.shp, <TENFILE>_LINES.shp, <TENFILE>_POLYGONS.shp.

        Các tham số đầu vào:
           - Tệp CAD (DGN/DXF/...): Đường dẫn đến tệp CAD đầu vào.
           - Ép CRS cho đầu ra (tùy chọn): Chọn CRS áp dụng cho các shapefile xuất ra (nếu để trống thì dùng CRS của dữ liệu gốc).
           - Thư mục xuất Shapefile: Nơi lưu kết quả các shapefile.
           - Tự động nạp các lớp sau khi xuất (ở dạng bộ nhớ): Nếu chọn, QGIS sẽ nạp bản trong bộ nhớ tạm của shapefile để hiển thị, tránh khóa file gốc.
           - In danh sách sublayer (tên/geom/Text/Level): Nếu chọn, thuật toán sẽ in ra danh sách tất cả các sublayer trong file CAD cùng thông tin kiểu hình học và có/không có trường LEVEL, TEXT.
           - Tách theo các Level: Nếu chọn, các shapefile sẽ được tách theo giá trị trong trường LEVEL (mỗi Level ra 1 shapefile riêng). Nếu bỏ chọn, mỗi loại hình học chỉ xuất 1 shapefile gộp chung.

        Trường trong shapefile đầu ra:
           - LEVEL: Giá trị Level gốc của đối tượng trong CAD.
           - SOURCE: Tên sublayer trong CAD.
           - TEXT: Nội dung text (nếu đối tượng là điểm có chữ).
           - ATTRS: Thuộc tính còn lại của feature, lưu ở dạng JSON rút gọn.

        *Lưu ý:
           - Kết quả chỉ tạo shapefile khi có đối tượng hợp lệ cho loại hình học đó.
           - DGN v8 cần GDAL có driver DGNv8, nếu không hãy đổi sang DXF hoặc DGN v7.
""")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(self.PARAM_INPUT, self.tr("Tệp CAD (DGN/DXF/...)"),
                                                     behavior=QgsProcessingParameterFile.File))
        self.addParameter(QgsProcessingParameterCrs(self.PARAM_CRS, self.tr("Ép CRS cho đầu ra (tùy chọn)"),
                                                    optional=True))
        self.addParameter(QgsProcessingParameterFolderDestination(self.PARAM_OUTDIR, self.tr("Thư mục xuất Shapefile")))
        self.addParameter(QgsProcessingParameterBoolean(self.PARAM_ADD, self.tr("Tự động nạp các lớp sau khi xuất (ở dạng memory)"),
                                                        defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(self.PARAM_LIST, self.tr("In danh sách sublayer"),
                                                        defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(self.PARAM_SPLITLV, self.tr("Tách theo các Level"),
                                                        defaultValue=True))
        self.addOutput(QgsProcessingOutputMultipleLayers(self.OUT_FILES, self.tr("Các file đã xuất")))

    # ---------- Helpers ----------
    def _slug_file(self, s: str):
        if not s: s = "LAYER"
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = re.sub(r"[^0-9A-Za-z_]+", "_", s).strip("_").upper() or "LAYER"
        return s

    def _invalidate_ogr(self):
        """Giải phóng kết nối/cache của OGR provider (nếu API hỗ trợ)."""
        try:
            reg = QgsProviderRegistry.instance()
            try:
                reg.invalidateConnections('ogr')
            except Exception:
                try:
                    md = reg.providerMetadata('ogr')
                    if hasattr(md, 'invalidateConnections'):
                        md.invalidateConnections()
                except Exception:
                    pass
        except Exception:
            pass
        gc.collect()

    def _list_sublayers(self, cad_path: str):
        try:
            ds = ogr.Open(cad_path)
        except RuntimeError as e:
            msg = str(e)
            if "recognized as a DGNv8 dataset" in msg and "DGNv8 driver is not available" in msg:
                raise QgsProcessingException(self.tr(
                    "Tệp là DGNv8 nhưng GDAL hiện tại không có driver DGNv8.\n"
                    "- Chuyển DGNv8 → DXF / DGN v7, hoặc\n- Cài GDAL/QGIS có DGNv8 driver.\nChi tiết: {}").format(msg))
            raise QgsProcessingException(self.tr("Không mở được tệp CAD: {}").format(msg))
        if ds is None:
            raise QgsProcessingException(self.tr("Không mở được tệp CAD (GDAL trả về None)."))
        out=[]
        try:
            for i in range(ds.GetLayerCount()):
                lyr = ds.GetLayerByIndex(i)
                if not lyr: continue
                name = lyr.GetName()
                uri = f"{cad_path}|layername={name}"
                out.append((name, uri))
        finally:
            lyr = None
            ds = None
        self._invalidate_ogr()
        return out

    def _open(self, uri: str):
        vl = QgsVectorLayer(uri, os.path.basename(uri), "ogr")
        return vl if vl and vl.isValid() else None

    def _find_text_field(self, vl: QgsVectorLayer):
        for c in ("Text","TEXT","text","Label","LABEL","label","String","STRING","RefName","REFNAME"):
            if vl.fields().indexOf(c) != -1:
                return c
        return None

    def _find_level_field(self, vl: QgsVectorLayer):
        for c in ("Level","LEVEL","level","LAYER","Layer","layer"):
            if vl.fields().indexOf(c) != -1:
                return c
        return None

    def _delete_shapefile_if_exists(self, shp: str):
        base,_=os.path.splitext(shp)
        for e in (".shp",".shx",".dbf",".prj",".cpg",".qpj",".sbn",".sbx",".fbn",".fbx",".ain",".aih",".ixs",".mxs",".atx",".shp.xml"):
            p=base+e
            try:
                if os.path.exists(p): os.remove(p)
            except Exception: pass

    def _mk_mem_layer(self, geom_name: str, crs_authid: str, name: str):
        """geom_name: 'Point' | 'LineString' | 'Polygon' (tránh convertToMultiType ở 3.40)."""
        vl = QgsVectorLayer(f"{geom_name}?crs={crs_authid or 'EPSG:4326'}", name, "memory")
        dp = vl.dataProvider()
        fields = QgsFields()
        fields.append(QgsField("LEVEL", QVariant.String, len=64))
        fields.append(QgsField("SOURCE", QVariant.String, len=64))
        if geom_name.lower().startswith("point"):
            fields.append(QgsField("TEXT", QVariant.String, len=254))
        fields.append(QgsField("ATTRS", QVariant.String, len=254))
        dp.addAttributes(list(fields)); vl.updateFields()
        return vl

    def _attrs_to_json254(self, feat: QgsFeature, exclude=("LEVEL","SOURCE","TEXT")):
        d={}
        flds=feat.fields()
        for i,v in enumerate(feat.attributes()):
            n=flds.at(i).name()
            if n in exclude: continue
            try: d[n]=v
            except Exception: d[n]=str(v)
        try: s=json.dumps(d, ensure_ascii=False)
        except Exception: s=str(d)
        return s[:254] if len(s)>254 else s

    def _force2d_valid(self, g: QgsGeometry):
        if not g or g.isEmpty(): return None
        gg = g.makeValid()
        try:
            gg = gg.force2D()
        except Exception:
            pass
        return gg

    def _find_created_shp(self, out_dir: str, candidates):
        pats=[]
        for n in candidates:
            if not n: continue
            pats += [os.path.join(out_dir, f"{n}.shp"),
                     os.path.join(out_dir, f"{n}*.shp"),
                     os.path.join(out_dir, f"{n.lower()}*.shp"),
                     os.path.join(out_dir, f"{n.upper()}*.shp")]
        for pat in pats:
            hits=glob.glob(pat)
            if hits:
                hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                return hits[0]
        return None

    def _write_with_ogr(self, vl: QgsVectorLayer, out_path: str):
        # Fallback, luôn đóng dataset/feature/layer.
        self._delete_shapefile_if_exists(out_path)
        drv = ogr.GetDriverByName("ESRI Shapefile")
        if drv is None: raise QgsProcessingException(self.tr("Không tìm thấy driver 'ESRI Shapefile'."))
        ds = drv.CreateDataSource(out_path)
        if ds is None: raise QgsProcessingException(self.tr("Không tạo được datasource: {}").format(out_path))

        gtype = QgsWkbTypes.geometryType(vl.wkbType())
        if gtype == QgsWkbTypes.PointGeometry: ogr_wkb = ogr.wkbPoint
        elif gtype == QgsWkbTypes.LineGeometry: ogr_wkb = ogr.wkbLineString
        elif gtype == QgsWkbTypes.PolygonGeometry: ogr_wkb = ogr.wkbPolygon
        else:
            ds=None; self._invalidate_ogr(); raise QgsProcessingException(self.tr("Loại hình học không hỗ trợ cho OGR fallback."))

        srs=None
        try:
            auth=vl.crs().authid() if vl.crs().isValid() else None
            if auth and auth.upper().startswith("EPSG:"):
                srs=osr.SpatialReference(); srs.ImportFromEPSG(int(auth.split(":")[1]))
        except Exception: srs=None

        base=os.path.splitext(os.path.basename(out_path))[0]
        layer = ds.CreateLayer(base, srs=srs, geom_type=ogr_wkb)
        if layer is None:
            ds=None; self._invalidate_ogr()
            raise QgsProcessingException(self.tr("Không tạo được lớp (OGR)."))

        def add_field(n,w):
            fd=ogr.FieldDefn(n, ogr.OFTString)
            try: fd.SetWidth(int(w))
            except Exception: pass
            layer.CreateField(fd)

        add_field("LEVEL",64); add_field("SOURCE",64)
        if gtype==QgsWkbTypes.PointGeometry: add_field("TEXT",254)
        add_field("ATTRS",254)

        idxL=vl.fields().indexOf("LEVEL")
        idxS=vl.fields().indexOf("SOURCE")
        idxT=vl.fields().indexOf("TEXT") if gtype==QgsWkbTypes.PointGeometry else -1
        idxA=vl.fields().indexOf("ATTRS")

        for f in vl.getFeatures():
            geom=f.geometry()
            if not geom or geom.isEmpty(): continue
            g=ogr.CreateGeometryFromWkb(bytes(geom.asWkb()))
            if g is None:
                try: g=ogr.CreateGeometryFromWkt(geom.asWkt())
                except Exception: continue
            feat=ogr.Feature(layer.GetLayerDefn())
            def safe(i):
                try: return f[i]
                except Exception: return None
            lv=safe(idxL); so=safe(idxS); tx=safe(idxT) if idxT!=-1 else None; at=safe(idxA)
            if lv is not None: feat.SetField("LEVEL", str(lv)[:64])
            if so is not None: feat.SetField("SOURCE", str(so)[:64])
            if idxT!=-1 and tx is not None: feat.SetField("TEXT", str(tx)[:254])
            if at is not None: feat.SetField("ATTRS", str(at)[:254])
            feat.SetGeometry(g)
            layer.CreateFeature(feat)
            feat = None  # đóng feature ngay

        # đóng mọi thứ
        layer = None
        ds = None
        self._invalidate_ogr()

        if not os.path.exists(out_path):
            raise QgsProcessingException(self.tr("OGR fallback: không thấy file sau khi ghi: {}").format(out_path))
        return out_path

    def _write_layer_shp(self, vl: QgsVectorLayer, out_path: str):
        """Ghi bằng QGIS API; nếu thất bại thì OGR fallback. Luôn giải phóng handle."""
        out_dir=os.path.dirname(out_path)
        if not os.path.isdir(out_dir):
            raise QgsProcessingException(self.tr("Thư mục không tồn tại: {}").format(out_dir))
        self._delete_shapefile_if_exists(out_path)

        base=os.path.splitext(os.path.basename(out_path))[0]
        mem_name=vl.name() or ""
        mem_slug=self._slug_file(mem_name)
        t0=time.time()

        # Cách 1: dir + layerName
        opts=QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName="ESRI Shapefile"
        opts.layerOptions=["ENCODING=UTF-8"]
        opts.layerName=base
        res1,err1=QgsVectorFileWriter.writeAsVectorFormatV2(
            vl, out_dir, QgsProject.instance().transformContext(), opts
        )
        self._invalidate_ogr()
        created=self._find_created_shp(out_dir,[base,mem_slug,mem_name])

        # Cách 2: đường dẫn trực tiếp
        if (res1!=QgsVectorFileWriter.NoError) or (created is None):
            self._delete_shapefile_if_exists(out_path)
            dest_crs = vl.crs() if vl.crs().isValid() else None
            if not dest_crs or not dest_crs.isValid():
                from qgis.core import QgsCoordinateReferenceSystem
                dest_crs=QgsCoordinateReferenceSystem("EPSG:4326")
            res2=QgsVectorFileWriter.writeAsVectorFormat(vl, out_path, "UTF-8", dest_crs, "ESRI Shapefile")
            self._invalidate_ogr()
            created=self._find_created_shp(out_dir,[base,mem_slug,mem_name])
            if created is None:
                recent=[p for p in glob.glob(os.path.join(out_dir,"*.shp")) if os.path.getmtime(p)>=t0]
                if recent:
                    recent.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                    created=recent[0]
            if (res2!=QgsVectorFileWriter.NoError) or (created is None):
                created=self._write_with_ogr(vl,out_path)  # OGR fallback đã tự invalidate

        return created

    def _add_to_project_as_memory(self, file_path: str, mem_name: str):
        """
        Tạo một bản sao layer MEMORY từ file_path và chỉ thêm bản memory vào project.
        Không nạp shapefile gốc để tránh khóa file trên Windows.
        """
        src = QgsVectorLayer(file_path, os.path.basename(file_path), "ogr")
        if not src or not src.isValid():
            self._invalidate_ogr()
            return

        # Xác định CRS + kiểu hình học
        crs_auth = src.crs().authid() if src.crs().isValid() else None
        gtype = QgsWkbTypes.geometryType(src.wkbType())
        if gtype == QgsWkbTypes.PointGeometry:
            geom_name = "Point"
        elif gtype == QgsWkbTypes.LineGeometry:
            geom_name = "LineString"
        elif gtype == QgsWkbTypes.PolygonGeometry:
            geom_name = "Polygon"
        else:
            src = None
            self._invalidate_ogr()
            return

        mem = QgsVectorLayer(f"{geom_name}?crs={crs_auth or 'EPSG:4326'}", mem_name, "memory")
        dp = mem.dataProvider()

        # ==== SAO CHÉP SCHEMA (tham số vị trí để tương thích 3.40) ====
        new_fields = []
        for f in src.fields():
            try:
                nf = QgsField(f.name(), f.type(), f.typeName(), int(f.length()), int(f.precision()))
            except TypeError:
                try:
                    nf = QgsField(f.name(), f.type(), int(f.length()), int(f.precision()))
                except TypeError:
                    nf = QgsField(f.name(), f.type())
            new_fields.append(nf)
        dp.addAttributes(new_fields)
        mem.updateFields()

        # ==== SAO CHÉP DỮ LIỆU ====
        feats_to_add = []
        for fe in src.getFeatures():
            nf = QgsFeature(mem.fields())
            nf.setGeometry(fe.geometry())
            nf.setAttributes(fe.attributes())
            feats_to_add.append(nf)
            if len(feats_to_add) >= 50000:
                dp.addFeatures(feats_to_add)
                feats_to_add.clear()
        if feats_to_add:
            dp.addFeatures(feats_to_add)

        # Thêm bản memory vào project
        QgsProject.instance().addMapLayer(mem)

        # Giải phóng mọi handle tới file
        src = None
        self._invalidate_ogr()

    def _split_mem_layer_by_level(self, mem_layer: QgsVectorLayer, geom_suffix: str,
                                  base_slug: str, out_dir: str, add_to_project: bool):
        """
        Tách một lớp MEMORY (points/lines/polygons) theo trường LEVEL rồi ghi từng nhóm ra Shapefile.
        Trả về danh sách đường dẫn file kết quả.
        """
        if not mem_layer: return []
        idxL = mem_layer.fields().indexOf("LEVEL")
        if idxL == -1: return []

        crs_auth = mem_layer.crs().authid() if mem_layer.crs().isValid() else "EPSG:4326"
        gtype = QgsWkbTypes.geometryType(mem_layer.wkbType())
        if gtype == QgsWkbTypes.PointGeometry:
            geom_name = "Point"
        elif gtype == QgsWkbTypes.LineGeometry:
            geom_name = "LineString"
        elif gtype == QgsWkbTypes.PolygonGeometry:
            geom_name = "Polygon"
        else:
            return []

        # Gom theo Level vào các lớp memory con
        groups = {}  # key_slug -> (display_value, mem_layer)
        for f in mem_layer.getFeatures():
            lv = f[idxL]
            disp = "EMPTY" if lv in (None, "") else str(lv)
            key = self._slug_file(disp)
            if key not in groups:
                sub = self._mk_mem_layer(geom_name, crs_auth, f"{base_slug}_{key}_{geom_suffix}_MEM")
                groups[key] = (disp, sub)
            sub = groups[key][1]
            nf = QgsFeature(sub.fields())
            nf.setGeometry(f.geometry())
            nf.setAttributes(f.attributes())
            sub.dataProvider().addFeature(nf)

        results = []
        # Ghi từng nhóm ra đĩa
        for key, (disp, sub_mem) in groups.items():
            out_path = os.path.join(out_dir, f"{base_slug}_{key}_{geom_suffix}.shp")
            real = self._write_layer_shp(sub_mem, out_path)
            results.append(real)
            if add_to_project:
                self._add_to_project_as_memory(real, f"{base_slug}_{key}_{geom_suffix}")

        return results

    # ---------- Core ----------
    def processAlgorithm(self, parameters, context, feedback):
        cad_path       = self.parameterAsFile(parameters, self.PARAM_INPUT, context)
        force_crs      = self.parameterAsCrs(parameters, self.PARAM_CRS, context)
        out_dir        = self.parameterAsFile(parameters, self.PARAM_OUTDIR, context)
        add_to_project = self.parameterAsBoolean(parameters, self.PARAM_ADD, context)
        list_sublayers = self.parameterAsBoolean(parameters, self.PARAM_LIST, context)
        split_by_level = self.parameterAsBoolean(parameters, self.PARAM_SPLITLV, context)

        if not os.path.isfile(cad_path): raise QgsProcessingException("Không tìm thấy tệp đầu vào.")
        if not out_dir or not os.path.isdir(out_dir): raise QgsProcessingException("Thư mục OUTPUT_DIR không hợp lệ.")

        subs=self._list_sublayers(cad_path)
        if not subs: raise QgsProcessingException("Không đọc được sublayers từ tệp CAD.")

        if list_sublayers:
            feedback.pushInfo("----- SUBLAYERS -----")
            for name,uri in subs:
                vl=self._open(uri)
                if not vl: feedback.pushInfo(f"{name}: (không mở được)"); continue
                gtype=QgsWkbTypes.geometryType(vl.wkbType())
                gstr={0:"Point",1:"Line",2:"Polygon"}.get(gtype,"Unknown/Mixed")
                has_text=bool(self._find_text_field(vl))
                has_level=bool(self._find_level_field(vl))
                feedback.pushInfo(f"{name} | geom≈{gstr} | TEXT={'Y' if has_text else 'N'} | LEVEL={'Y' if has_level else 'N'}")
                vl = None
            feedback.pushInfo("---------------------")
            self._invalidate_ogr()

        in_base=os.path.splitext(os.path.basename(cad_path))[0]
        base_slug=self._slug_file(in_base)
        crs_auth=(force_crs and force_crs.authid()) or None

        # Bộ gom
        points_mem = lines_mem = polys_mem = None
        n_pt = n_ln = n_pg = 0

        for sub_name, uri in subs:
            vl=self._open(uri)
            if not vl: continue
            crs_out = crs_auth or vl.crs().authid() or "EPSG:4326"
            text_field = self._find_text_field(vl)
            level_field= self._find_level_field(vl)

            for f in vl.getFeatures():
                geom=f.geometry()
                if not geom or geom.isEmpty(): continue

                ftype = geom.type()  # 0=Point,1=Line,2=Polygon

                try: level_val = f[level_field] if level_field else None
                except Exception: level_val=None
                if level_val in (None,""): level_val=sub_name

                if ftype == QgsWkbTypes.PointGeometry:
                    if points_mem is None:
                        points_mem = self._mk_mem_layer("Point", crs_out, f"{base_slug}_POINTS_MEM")
                    nf=QgsFeature(points_mem.fields())
                    nf.setGeometry(self._force2d_valid(geom))
                    txt=""
                    if text_field:
                        try:
                            tv=f[text_field]; txt="" if tv is None else str(tv)[:254]
                        except Exception: txt=""
                    nf.setAttributes([
                        str(level_val)[:64],
                        str(sub_name)[:64],
                        txt,
                        self._attrs_to_json254(f)
                    ])
                    points_mem.dataProvider().addFeature(nf); n_pt+=1

                elif ftype == QgsWkbTypes.LineGeometry:
                    if lines_mem is None:
                        lines_mem = self._mk_mem_layer("LineString", crs_out, f"{base_slug}_LINES_MEM")
                    g2=self._force2d_valid(geom)
                    if not g2 or g2.isEmpty(): continue
                    nf=QgsFeature(lines_mem.fields()); nf.setGeometry(g2)
                    nf.setAttributes([
                        str(level_val)[:64],
                        str(sub_name)[:64],
                        self._attrs_to_json254(f)
                    ])
                    lines_mem.dataProvider().addFeature(nf); n_ln+=1

                elif ftype == QgsWkbTypes.PolygonGeometry:
                    if polys_mem is None:
                        polys_mem = self._mk_mem_layer("Polygon", crs_out, f"{base_slug}_POLYGONS_MEM")
                    g2=self._force2d_valid(geom)
                    if not g2 or g2.isEmpty(): continue
                    nf=QgsFeature(polys_mem.fields()); nf.setGeometry(g2)
                    nf.setAttributes([
                        str(level_val)[:64],
                        str(sub_name)[:64],
                        self._attrs_to_json254(f)
                    ])
                    polys_mem.dataProvider().addFeature(nf); n_pg+=1
                else:
                    continue

            vl = None  # đóng tham chiếu lớp sublayer

        result=[]

        # ======= Ghi ra đĩa =======
        try:
            if split_by_level:
                # Tách trực tiếp từ bộ nhớ để tránh ghi 2 lần và tăng tốc
                if points_mem and n_pt>0:
                    feedback.pushInfo(self.tr(f"TÁCH THEO LEVEL: POINTS ({n_pt} đối tượng)..."))
                    res = self._split_mem_layer_by_level(points_mem, "POINTS", base_slug, out_dir, add_to_project)
                    result.extend(res)
                if lines_mem and n_ln>0:
                    feedback.pushInfo(self.tr(f"TÁCH THEO LEVEL: LINES ({n_ln} đối tượng)..."))
                    res = self._split_mem_layer_by_level(lines_mem, "LINES", base_slug, out_dir, add_to_project)
                    result.extend(res)
                if polys_mem and n_pg>0:
                    feedback.pushInfo(self.tr(f"TÁCH THEO LEVEL: POLYGONS ({n_pg} đối tượng)..."))
                    res = self._split_mem_layer_by_level(polys_mem, "POLYGONS", base_slug, out_dir, add_to_project)
                    result.extend(res)
            else:
                # Hành vi cũ: ghi gộp theo hình học
                if points_mem and n_pt>0:
                    feedback.pushInfo(self.tr(f"POINTS: chuẩn bị ghi {n_pt} đối tượng..."))
                    out_pts=os.path.join(out_dir, f"{base_slug}_POINTS.shp")
                    real=self._write_layer_shp(points_mem, out_pts); result.append(real)
                    if add_to_project:
                        self._add_to_project_as_memory(real, f"{base_slug}_POINTS")
                    feedback.pushInfo(self.tr(f"Đã xuất: {real}"))

                if lines_mem and n_ln>0:
                    feedback.pushInfo(self.tr(f"LINES: chuẩn bị ghi {n_ln} đối tượng..."))
                    out_lin=os.path.join(out_dir, f"{base_slug}_LINES.shp")
                    real=self._write_layer_shp(lines_mem, out_lin); result.append(real)
                    if add_to_project:
                        self._add_to_project_as_memory(real, f"{base_slug}_LINES")
                    feedback.pushInfo(self.tr(f"Đã xuất: {real}"))

                if polys_mem and n_pg>0:
                    feedback.pushInfo(self.tr(f"POLYGONS: chuẩn bị ghi {n_pg} đối tượng..."))
                    out_pol=os.path.join(out_dir, f"{base_slug}_POLYGONS.shp")
                    real=self._write_layer_shp(polys_mem, out_pol); result.append(real)
                    if add_to_project:
                        self._add_to_project_as_memory(real, f"{base_slug}_POLYGONS")
                    feedback.pushInfo(self.tr(f"Đã xuất: {real}"))
        finally:
            # BỎ tham chiếu tới memory layers dùng để gom → giải phóng ngay
            points_mem = None
            lines_mem  = None
            polys_mem  = None
            self._invalidate_ogr()

        if not result:
            feedback.pushInfo(self.tr("Không có đối tượng nào để xuất (có thể mọi feature không có hình học hợp lệ)."))

        return { self.OUT_FILES: result }
