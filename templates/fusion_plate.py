"""Structured parametric plate task, executed only inside Autodesk Fusion.

The MCP adapter replaces PARAMETERS and OUTPUT_DIR with validated literals.
Fusion's native geometry API uses centimeters; public parameters use millimeters.
"""
from pathlib import Path
import json
import math
import struct
import adsk.core
import adsk.fusion

PARAMETERS = None
OUTPUT_DIR = None

_building_document = None


def point(x, y, z=0):
    return adsk.core.Point3D.create(x, y, z)


def expression(value):
    return adsk.core.ValueInput.createByString(value)


def _parameters():
    names = ("length_mm", "width_mm", "thickness_mm", "hole_diameter_mm",
             "inset_x_mm", "inset_y_mm", "corner_radius_mm")
    if not isinstance(PARAMETERS, dict) or OUTPUT_DIR is None:
        raise ValueError("The MCP adapter must supply PARAMETERS and OUTPUT_DIR")
    values = {name: float(PARAMETERS[name]) for name in names}
    if any(not math.isfinite(value) or value <= 0 for value in values.values()):
        raise ValueError("All dimensions must be finite and positive")
    length, width = values["length_mm"], values["width_mm"]
    diameter = values["hole_diameter_mm"]
    ix, iy, radius = values["inset_x_mm"], values["inset_y_mm"], values["corner_radius_mm"]
    if radius >= min(length, width) / 2:
        raise ValueError("Corner radius must be smaller than half the shorter side")
    if min(ix, iy) <= diameter / 2:
        raise ValueError("Hole edges must lie inside the plate boundary")
    if length - 2 * ix <= diameter or width - 2 * iy <= diameter:
        raise ValueError("The four holes must be distinct and must not touch or overlap")
    if ix < radius and iy < radius and math.hypot(radius - ix, radius - iy) + diameter / 2 >= radius:
        raise ValueError("A hole would intersect a rounded corner")
    return values


def _verify_stl(path, expected_size_mm, expected_volume_mm3):
    data = path.read_bytes()
    if len(data) < 84:
        raise RuntimeError("STL export is too short")
    count = struct.unpack_from("<I", data, 80)[0]
    if count == 0 or len(data) != 84 + 50 * count:
        raise RuntimeError("STL is not a valid nonempty binary STL")
    minima, maxima = [math.inf] * 3, [-math.inf] * 3
    edges = {}
    signed_volume = 0.0
    for index in range(count):
        item = struct.unpack_from("<12fH", data, 84 + index * 50)
        vertices = [tuple(item[start:start + 3]) for start in (3, 6, 9)]
        if any(not math.isfinite(value) for vertex in vertices for value in vertex):
            raise RuntimeError("STL contains nonfinite coordinates")
        for vertex in vertices:
            for axis, value in enumerate(vertex):
                minima[axis] = min(minima[axis], value)
                maxima[axis] = max(maxima[axis], value)
        a, b, c = vertices
        signed_volume += (a[0] * (b[1] * c[2] - b[2] * c[1])
                          + a[1] * (b[2] * c[0] - b[0] * c[2])
                          + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
        # Shared binary-STL vertices should have exactly matching float values.
        for left, right in ((a, b), (b, c), (c, a)):
            edge = tuple(sorted((left, right)))
            edges[edge] = edges.get(edge, 0) + 1
    size = [maximum - minimum for minimum, maximum in zip(minima, maxima)]
    if any(abs(actual - expected) > max(1e-4, abs(expected) * 1e-6)
           for actual, expected in zip(size, expected_size_mm)):
        raise RuntimeError("STL dimensions do not match millimeter coordinates: " + str(size))
    if any(value != 2 for value in edges.values()):
        raise RuntimeError("STL is not a closed two-manifold triangle mesh")
    volume = abs(signed_volume)
    relative_error = abs(volume - expected_volume_mm3) / expected_volume_mm3
    if relative_error > 0.005:
        raise RuntimeError("STL volume differs from the solid by more than 0.5 percent")
    return {"coordinate_unit": "mm", "triangles": count, "dimensions_mm": size,
            "closed_two_manifold": True, "volume_mm3": volume,
            "relative_volume_error": relative_error}


def _build(context):
    global _building_document
    values = _parameters()
    length, width, thickness = (values[name] for name in ("length_mm", "width_mm", "thickness_mm"))
    diameter, ix, iy, radius = (values[name] for name in
                               ("hole_diameter_mm", "inset_x_mm", "inset_y_mm", "corner_radius_mm"))
    lx, wy, tz, hx, hy = length / 10, width / 10, thickness / 10, ix / 10, iy / 10
    directory = Path(OUTPUT_DIR).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    paths = {extension: directory / ("plate." + extension) for extension in ("f3d", "step", "stl")}
    image_path = directory / "isometric.png"
    report_path = directory / "verification.json"
    for path in [*paths.values(), image_path, report_path]:
        if path.exists():
            raise FileExistsError("An output already exists: " + str(path))

    app = adsk.core.Application.get()
    document = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    _building_document = document
    document.name = f"MCP mounting plate {length:g}x{width:g}x{thickness:g} mm"
    design = adsk.fusion.Design.cast(app.activeProduct)
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    design.fusionUnitsManager.distanceDisplayUnits = adsk.fusion.DistanceUnits.MillimeterDistanceUnits
    root = design.rootComponent
    parameter_specs = [
        ("plate_length", length, "Plate length along X"),
        ("plate_width", width, "Plate width along Y"),
        ("plate_thickness", thickness, "Thickness along positive Z"),
        ("hole_diameter", diameter, "Diameter of four through holes"),
        ("hole_inset_x", ix, "Hole center inset from left and right edges"),
        ("hole_inset_y", iy, "Hole center inset from lower and upper edges"),
        ("corner_radius", radius, "Radius of only the four outer vertical edges"),
    ]
    for name, value, comment in parameter_specs:
        design.userParameters.add(name, expression(f"{value:.15g} mm"), "mm", comment)

    outline = root.sketches.add(root.xYConstructionPlane)
    outline.name = "01_Plate_outline_fully_constrained"
    lines = outline.sketchCurves.sketchLines.addTwoPointRectangle(outline.originPoint, point(lx, wy))
    for line in lines:
        a, b = line.startSketchPoint.geometry, line.endSketchPoint.geometry
        if abs(a.y - b.y) < 1e-8:
            outline.geometricConstraints.addHorizontal(line)
        else:
            outline.geometricConstraints.addVertical(line)
    horizontal = adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation
    vertical = adsk.fusion.DimensionOrientations.VerticalDimensionOrientation
    outline.sketchDimensions.addDistanceDimension(lines.item(0).startSketchPoint,
        lines.item(0).endSketchPoint, horizontal, point(lx / 2, -wy / 5)).parameter.expression = "plate_length"
    outline.sketchDimensions.addDistanceDimension(lines.item(1).startSketchPoint,
        lines.item(1).endSketchPoint, vertical, point(lx * 1.2, wy / 2)).parameter.expression = "plate_width"
    if not outline.isFullyConstrained:
        raise RuntimeError("Plate outline is not fully constrained")
    base = root.features.extrudeFeatures.addSimple(outline.profiles.item(0), expression("plate_thickness"),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    base.name = "02_Plate_thickness"
    base.bodies.item(0).name = "Mounting_Plate"
    outline.isVisible = False

    holes = root.sketches.add(root.xYConstructionPlane)
    holes.name = "03_Four_holes_fully_constrained"
    centers = [(hx, hy), (lx - hx, hy), (hx, wy - hy), (lx - hx, wy - hy)]
    layout = holes.sketchCurves.sketchLines.addTwoPointRectangle(point(hx, hy), point(lx - hx, wy - hy))
    for line in layout:
        line.isConstruction = True
        a, b = line.startSketchPoint.geometry, line.endSketchPoint.geometry
        if abs(a.y - b.y) < 1e-8:
            holes.geometricConstraints.addHorizontal(line)
        else:
            holes.geometricConstraints.addVertical(line)
    holes.sketchDimensions.addDistanceDimension(layout.item(0).startSketchPoint, layout.item(0).endSketchPoint,
        horizontal, point(lx / 2, hy / 2)).parameter.expression = "plate_length-2*hole_inset_x"
    holes.sketchDimensions.addDistanceDimension(layout.item(1).startSketchPoint, layout.item(1).endSketchPoint,
        vertical, point(lx - hx / 2, wy / 2)).parameter.expression = "plate_width-2*hole_inset_y"
    holes.sketchDimensions.addDistanceDimension(holes.originPoint, layout.item(0).startSketchPoint,
        horizontal, point(hx / 2, -hy / 2)).parameter.expression = "hole_inset_x"
    holes.sketchDimensions.addDistanceDimension(holes.originPoint, layout.item(0).startSketchPoint,
        vertical, point(-hx / 2, hy / 2)).parameter.expression = "hole_inset_y"
    for line in layout:
        center = line.startSketchPoint
        x, y = center.geometry.x, center.geometry.y
        circle = holes.sketchCurves.sketchCircles.addByCenterRadius(center, diameter / 20)
        holes.sketchDimensions.addDiameterDimension(circle, point(x + diameter / 10, y + diameter / 10)).parameter.expression = "hole_diameter"
    if not holes.isFullyConstrained or holes.profiles.count != 4:
        raise RuntimeError("Expected four fully constrained hole profiles")
    profiles = adsk.core.ObjectCollection.create()
    for profile in holes.profiles:
        profiles.add(profile)
    cut_input = root.features.extrudeFeatures.createInput(profiles, adsk.fusion.FeatureOperations.CutFeatureOperation)
    cut_input.setOneSideExtent(adsk.fusion.ThroughAllExtentDefinition.create(),
                              adsk.fusion.ExtentDirections.PositiveExtentDirection)
    root.features.extrudeFeatures.add(cut_input).name = "04_Four_through_holes"
    holes.isVisible = False

    body = root.bRepBodies.item(0)
    corner_edges = adsk.core.ObjectCollection.create()
    for edge in body.edges:
        if not edge.startVertex or not edge.endVertex:
            continue
        a, b = edge.startVertex.geometry, edge.endVertex.geometry
        if (abs(a.x - b.x) < 1e-7 and abs(a.y - b.y) < 1e-7 and abs(abs(a.z - b.z) - tz) < 1e-7
                and min(abs(a.x), abs(a.x - lx)) < 1e-7
                and min(abs(a.y), abs(a.y - wy)) < 1e-7):
            corner_edges.add(edge)
    if corner_edges.count != 4:
        raise RuntimeError(f"Expected four outside vertical edges, found {corner_edges.count}")
    fillet_input = root.features.filletFeatures.createInput()
    fillet_input.edgeSetInputs.addConstantRadiusEdgeSet(corner_edges, expression("corner_radius"), False)
    root.features.filletFeatures.add(fillet_input).name = "05_Outer_vertical_corner_fillets"
    design.computeAll()

    if root.bRepBodies.count != 1 or not root.bRepBodies.item(0).isSolid:
        raise RuntimeError("Final geometry must be one solid body")
    body = root.bRepBodies.item(0)
    bounds = body.boundingBox
    size_mm = [(bounds.maxPoint.x - bounds.minPoint.x) * 10,
               (bounds.maxPoint.y - bounds.minPoint.y) * 10,
               (bounds.maxPoint.z - bounds.minPoint.z) * 10]
    if any(abs(actual - expected) > 1e-5 for actual, expected in zip(size_mm, (length, width, thickness))):
        raise RuntimeError("Overall dimensions are incorrect: " + str(size_mm))
    if any(abs(value) > 1e-6 for value in (bounds.minPoint.x, bounds.minPoint.y, bounds.minPoint.z)):
        raise RuntimeError("The underside lower-left origin is not (0,0,0)")
    expected_volume = (length * width - (4 - math.pi) * radius ** 2 - math.pi * diameter ** 2) * thickness
    volume_mm3 = body.volume * 1000
    if abs(volume_mm3 - expected_volume) > max(1e-4, expected_volume * 1e-9):
        raise RuntimeError(f"Solid volume failed: {volume_mm3} versus {expected_volume}")
    hole_centers_mm = [(x * 10, y * 10) for x, y in centers]
    fillet_centers_mm = [(radius, radius), (length - radius, radius),
                         (radius, width - radius), (length - radius, width - radius)]
    cylinders = []
    for face in body.faces:
        cylinder = adsk.core.Cylinder.cast(face.geometry)
        if cylinder:
            cylinders.append({"x": cylinder.origin.x * 10, "y": cylinder.origin.y * 10,
                "radius": cylinder.radius * 10, "z_min": face.boundingBox.minPoint.z * 10,
                "z_max": face.boundingBox.maxPoint.z * 10})
    for expected_radius, positions, label in ((diameter / 2, hole_centers_mm, "hole"),
                                               (radius, fillet_centers_mm, "corner fillet")):
        for x, y in positions:
            matches = [item for item in cylinders if abs(item["x"] - x) < 1e-5 and abs(item["y"] - y) < 1e-5
                       and abs(item["radius"] - expected_radius) < 1e-5]
            if len(matches) != 1 or abs(matches[0]["z_min"]) > 1e-5 or abs(matches[0]["z_max"] - thickness) > 1e-5:
                raise RuntimeError(f"Incorrect {label} cylinder at {(x, y)} mm")
    if len(cylinders) != 8:
        raise RuntimeError("Expected exactly four hole and four corner cylinders")
    for x, y in centers:
        for fraction in (0.1, 0.5, 0.9):
            if body.pointContainment(point(x, y, tz * fraction)) != adsk.fusion.PointContainment.PointOutsidePointContainment:
                raise RuntimeError("A requested through hole is obstructed")

    timeline = []
    for index in range(design.timeline.count):
        entity = design.timeline.item(index).entity
        if hasattr(entity, "healthState") and entity.healthState != adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState:
            raise RuntimeError(f"Unhealthy feature {entity.name}: {entity.errorOrWarningMessage}")
        timeline.append(entity.name)
    if len(timeline) != 5 or not outline.isFullyConstrained or not holes.isFullyConstrained:
        raise RuntimeError("Parametric history / sketch verification failed")

    app.userInterface.activeSelections.clear()
    viewport = app.activeViewport
    viewport.visualStyle = adsk.core.VisualStyles.ShadedWithVisibleEdgesOnlyVisualStyle
    camera = viewport.camera
    camera.cameraType = adsk.core.CameraTypes.OrthographicCameraType
    camera.target = point(lx / 2, wy / 2, tz / 2)
    distance = max(lx, wy, tz)
    camera.eye = point(lx / 2 + distance * 1.5, wy / 2 - distance * 1.5, tz / 2 + distance * 1.5)
    camera.upVector = adsk.core.Vector3D.create(-1, 1, 2)
    camera.isSmoothTransition = False
    camera.isFitView = True
    viewport.camera = camera
    viewport.refresh()
    if not viewport.saveAsImageFile(str(image_path), 1600, 1100):
        raise RuntimeError("Native isometric image export failed")
    manager = design.exportManager
    if not manager.execute(manager.createFusionArchiveExportOptions(str(paths["f3d"]))):
        raise RuntimeError("F3D export failed")
    if not manager.execute(manager.createSTEPExportOptions(str(paths["step"]))):
        raise RuntimeError("STEP export failed")
    stl = manager.createSTLExportOptions(body, str(paths["stl"]))
    stl.unitType = adsk.fusion.DistanceUnits.MillimeterDistanceUnits
    stl.isBinaryFormat = True
    stl.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
    if not manager.execute(stl):
        raise RuntimeError("STL export failed")
    for path in [*paths.values(), image_path]:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("Missing export: " + str(path))
    stl_check = _verify_stl(paths["stl"], [length, width, thickness], expected_volume)
    report = {"software": "Autodesk Fusion", "version": app.version, "parameters": values,
        "dimensions_mm": size_mm, "origin_mm": [0, 0, 0], "bodies": 1,
        "hole_centers_mm": hole_centers_mm, "holes_through_all": True,
        "hole_diameter_mm": diameter, "external_corner_radius_mm": radius,
        "volume_mm3": volume_mm3, "analytic_volume_mm3": expected_volume,
        "sketches_fully_constrained": [True, True], "timeline": timeline,
        "parametric_history": True,
        "user_parameters_mm": {name: design.userParameters.itemByName(name).value * 10 for name, _, _ in parameter_specs},
        "STL": stl_check, "isometric_image": str(image_path),
        "files": {key: str(path) for key, path in paths.items()}, "checks_passed": True}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["verification_file"] = str(report_path)
    return report


def run(context):
    try:
        return _build(context)
    except Exception:
        if _building_document:
            _building_document.close(False)
        raise
