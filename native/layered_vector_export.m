#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>

static NSArray<NSString *> *ReadSymbolsFile(NSString *path) {
    NSString *contents = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
    if (!contents) {
        return nil;
    }
    NSMutableArray *symbols = [NSMutableArray array];
    for (NSString *line in [contents componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
        NSString *symbol = [line stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        if (symbol.length > 0 && ![symbol hasPrefix:@"#"]) {
            [symbols addObject:symbol];
        }
    }
    return symbols;
}

static NSString *JSONString(id object) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:object options:0 error:nil];
    return [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
}

static NSMutableDictionary *JSONPoint(CGPoint p) {
    return [@{@"x": @(p.x), @"y": @(p.y)} mutableCopy];
}

static void CollectElement(void *info, const CGPathElement *element) {
    NSMutableArray *elements = (__bridge NSMutableArray *)info;
    NSMutableDictionary *entry = [NSMutableDictionary dictionary];
    switch (element->type) {
        case kCGPathElementMoveToPoint:
            entry[@"type"] = @"move";
            entry[@"points"] = @[JSONPoint(element->points[0])];
            break;
        case kCGPathElementAddLineToPoint:
            entry[@"type"] = @"line";
            entry[@"points"] = @[JSONPoint(element->points[0])];
            break;
        case kCGPathElementAddQuadCurveToPoint:
            entry[@"type"] = @"quad";
            entry[@"points"] = @[JSONPoint(element->points[0]), JSONPoint(element->points[1])];
            break;
        case kCGPathElementAddCurveToPoint:
            entry[@"type"] = @"curve";
            entry[@"points"] = @[JSONPoint(element->points[0]), JSONPoint(element->points[1]), JSONPoint(element->points[2])];
            break;
        case kCGPathElementCloseSubpath:
            entry[@"type"] = @"close";
            entry[@"points"] = @[];
            break;
    }
    [elements addObject:entry];
}

static void CountElement(void *info, const CGPathElement *element) {
    NSUInteger *value = (NSUInteger *)info;
    (*value)++;
}

static CGPathRef MsgSendCGPath(id target, SEL selector);

static NSUInteger PathElementCount(CGPathRef path) {
    if (!path) {
        return 0;
    }
    NSUInteger count = 0;
    CGPathApply(path, &count, CountElement);
    return count;
}

typedef struct {
    NSMutableArray *points;
} PointCollector;

static void CollectFlattenedPoint(void *info, const CGPathElement *element) {
    PointCollector *collector = (PointCollector *)info;
    switch (element->type) {
        case kCGPathElementMoveToPoint:
        case kCGPathElementAddLineToPoint:
            [collector->points addObject:[NSValue valueWithPoint:NSMakePoint(element->points[0].x, element->points[0].y)]];
            break;
        default:
            break;
    }
}

static NSArray<NSValue *> *FlattenedPoints(CGPathRef path) {
    CGPathRef flattened = CGPathCreateCopyByFlattening(path, 0.05);
    PointCollector collector = { [NSMutableArray array] };
    CGPathApply(flattened ?: path, &collector, CollectFlattenedPoint);
    if (flattened) {
        CGPathRelease(flattened);
    }
    return collector.points;
}

static double PathSignedArea(CGPathRef path) {
    NSArray<NSValue *> *points = FlattenedPoints(path);
    if (points.count < 3) {
        return 0;
    }
    double area = 0;
    for (NSUInteger i = 0; i < points.count; i++) {
        NSPoint a = points[i].pointValue;
        NSPoint b = points[(i + 1) % points.count].pointValue;
        area += a.x * b.y - b.x * a.y;
    }
    return area / 2.0;
}

static CGPoint PathCenter(CGPathRef path) {
    CGRect box = CGPathGetPathBoundingBox(path);
    return CGPointMake(CGRectGetMidX(box), CGRectGetMidY(box));
}

static BOOL PathContainsPathCenter(CGPathRef container, CGPathRef child) {
    return CGPathContainsPoint(container, NULL, PathCenter(child), false);
}

static BOOL PathStronglyContainsPath(CGPathRef container, CGPathRef child) {
    NSArray<NSValue *> *points = FlattenedPoints(child);
    if (points.count == 0) {
        return NO;
    }
    NSUInteger inside = 0;
    for (NSValue *value in points) {
        NSPoint point = value.pointValue;
        if (CGPathContainsPoint(container, NULL, CGPointMake(point.x, point.y), false)) {
            inside++;
        }
    }
    return (double)inside / (double)points.count >= 0.8;
}

static CGPathRef CopyUnionOfPaths(NSArray *paths) {
    if (paths.count == 0) {
        return NULL;
    }
    CGPathRef result = CGPathRetain((__bridge CGPathRef)paths[0]);
    for (NSUInteger i = 1; i < paths.count; i++) {
        CGPathRef next = (__bridge CGPathRef)paths[i];
        CGPathRef unioned = CGPathCreateCopyByUnioningPath(result, next, true);
        if (unioned) {
            CGPathRelease(result);
            result = unioned;
        }
    }
    return result;
}

static CGPathRef CopyShapeGroupBooleanPath(NSArray *shapeGroupSubpaths) {
    NSMutableArray *paths = [NSMutableArray array];
    NSMutableArray *areas = [NSMutableArray array];
    for (id pathObject in shapeGroupSubpaths) {
        CGPathRef path = MsgSendCGPath(pathObject, NSSelectorFromString(@"path"));
        if (!path) {
            continue;
        }
        [paths addObject:(__bridge id)path];
        [areas addObject:@(PathSignedArea(path))];
    }
    if (paths.count == 0) {
        return NULL;
    }

    NSUInteger rootIndex = 0;
    double rootAbsArea = 0;
    for (NSUInteger i = 0; i < paths.count; i++) {
        double absArea = fabs([areas[i] doubleValue]);
        if (absArea > rootAbsArea) {
            rootAbsArea = absArea;
            rootIndex = i;
        }
    }

    CGPathRef root = (__bridge CGPathRef)paths[rootIndex];
    BOOL rootNegative = [areas[rootIndex] doubleValue] < 0;
    NSMutableArray *subtractCandidates = [NSMutableArray array];
    NSMutableArray *addPaths = [NSMutableArray array];
    for (NSUInteger i = 0; i < paths.count; i++) {
        if (i == rootIndex) {
            continue;
        }
        CGPathRef path = (__bridge CGPathRef)paths[i];
        if (!PathContainsPathCenter(root, path)) {
            continue;
        }
        BOOL negative = [areas[i] doubleValue] < 0;
        if (negative == rootNegative) {
            [subtractCandidates addObject:paths[i]];
        } else {
            [addPaths addObject:paths[i]];
        }
    }

    NSMutableArray *subtractMasks = [NSMutableArray array];
    for (id candidateObject in subtractCandidates) {
        CGPathRef candidate = (__bridge CGPathRef)candidateObject;
        BOOL containsProtectedAddPath = NO;
        for (id addObject in addPaths) {
            if (PathStronglyContainsPath(candidate, (__bridge CGPathRef)addObject)) {
                containsProtectedAddPath = YES;
                break;
            }
        }
        BOOL isOuterSameWindingContainer = NO;
        for (id otherObject in subtractCandidates) {
            if (otherObject == candidateObject) {
                continue;
            }
            CGPathRef other = (__bridge CGPathRef)otherObject;
            if (!containsProtectedAddPath &&
                fabs(PathSignedArea(candidate)) > fabs(PathSignedArea(other)) &&
                PathStronglyContainsPath(candidate, other)) {
                isOuterSameWindingContainer = YES;
                break;
            }
        }
        if (!isOuterSameWindingContainer) {
            [subtractMasks addObject:candidateObject];
        }
    }

    CGPathRef subtractUnion = CopyUnionOfPaths(subtractMasks);
    CGPathRef result = subtractUnion ? CGPathCreateCopyBySubtractingPath(root, subtractUnion, true) : CGPathRetain(root);
    if (subtractUnion) {
        CGPathRelease(subtractUnion);
    }

    for (id addObject in addPaths) {
        CGPathRef addPath = (__bridge CGPathRef)addObject;
        NSMutableArray *addMasks = [NSMutableArray array];
        for (id maskObject in subtractMasks) {
            CGPathRef maskPath = (__bridge CGPathRef)maskObject;
            if (!PathStronglyContainsPath(maskPath, addPath)) {
                [addMasks addObject:maskObject];
            }
        }
        CGPathRef addMaskUnion = CopyUnionOfPaths(addMasks);
        CGPathRef clippedAdd = addMaskUnion ? CGPathCreateCopyBySubtractingPath(addPath, addMaskUnion, true) : CGPathRetain(addPath);
        if (addMaskUnion) {
            CGPathRelease(addMaskUnion);
        }
        CGPathRef unioned = CGPathCreateCopyByUnioningPath(result, clippedAdd, true);
        if (unioned) {
            CGPathRelease(result);
            result = unioned;
        }
        CGPathRelease(clippedAdd);
    }

    return result;
}

static CGPathRef CopyShapeGroupDominantSubtractPath(NSArray *shapeGroupSubpaths) {
    NSMutableArray *paths = [NSMutableArray array];
    NSMutableArray *areas = [NSMutableArray array];
    for (id pathObject in shapeGroupSubpaths) {
        CGPathRef path = MsgSendCGPath(pathObject, NSSelectorFromString(@"path"));
        if (!path) {
            continue;
        }
        [paths addObject:(__bridge id)path];
        [areas addObject:@(fabs(PathSignedArea(path)))];
    }
    if (paths.count == 0) {
        return NULL;
    }

    NSUInteger rootIndex = 0;
    double rootArea = 0;
    for (NSUInteger i = 0; i < paths.count; i++) {
        double area = [areas[i] doubleValue];
        if (area > rootArea) {
            rootArea = area;
            rootIndex = i;
        }
    }

    CGPathRef root = (__bridge CGPathRef)paths[rootIndex];
    NSMutableArray *cutouts = [NSMutableArray array];
    NSMutableArray *addPaths = [NSMutableArray array];
    for (NSUInteger i = 0; i < paths.count; i++) {
        if (i == rootIndex) {
            continue;
        }
        CGPathRef path = (__bridge CGPathRef)paths[i];
        if (PathContainsPathCenter(root, path)) {
            [cutouts addObject:paths[i]];
        } else {
            [addPaths addObject:paths[i]];
        }
    }

    CGPathRef cutoutUnion = CopyUnionOfPaths(cutouts);
    CGPathRef result = cutoutUnion ? CGPathCreateCopyBySubtractingPath(root, cutoutUnion, true) : CGPathRetain(root);
    if (cutoutUnion) {
        CGPathRelease(cutoutUnion);
    }

    for (id addObject in addPaths) {
        CGPathRef addPath = (__bridge CGPathRef)addObject;
        CGPathRef unioned = CGPathCreateCopyByUnioningPath(result, addPath, true);
        if (unioned) {
            CGPathRelease(result);
            result = unioned;
        }
    }

    return result;
}

static NSUInteger NearestContainingParent(NSUInteger child, NSArray *paths, NSArray *areas) {
    CGPathRef childPath = (__bridge CGPathRef)paths[child];
    double childArea = [areas[child] doubleValue];
    NSUInteger parent = NSNotFound;
    double parentArea = DBL_MAX;
    for (NSUInteger i = 0; i < paths.count; i++) {
        if (i == child) {
            continue;
        }
        double area = [areas[i] doubleValue];
        if (area <= childArea || area >= parentArea) {
            continue;
        }
        CGPathRef candidate = (__bridge CGPathRef)paths[i];
        if (PathStronglyContainsPath(candidate, childPath)) {
            parent = i;
            parentArea = area;
        }
    }
    return parent;
}

static CGPathRef CopySubtractUnion(CGPathRef source, NSArray *masks) {
    CGPathRef maskUnion = CopyUnionOfPaths(masks);
    CGPathRef result = maskUnion ? CGPathCreateCopyBySubtractingPath(source, maskUnion, true) : CGPathRetain(source);
    if (maskUnion) {
        CGPathRelease(maskUnion);
    }
    return result;
}

static CGPathRef CopyShapeGroupPaintFlattenedPath(NSArray *shapeGroupSubpaths) {
    NSMutableArray *paths = [NSMutableArray array];
    NSMutableArray *areas = [NSMutableArray array];
    NSMutableArray *signedAreas = [NSMutableArray array];
    for (id pathObject in shapeGroupSubpaths) {
        CGPathRef path = MsgSendCGPath(pathObject, NSSelectorFromString(@"path"));
        if (!path) {
            continue;
        }
        double signedArea = PathSignedArea(path);
        [paths addObject:(__bridge id)path];
        [areas addObject:@(fabs(signedArea))];
        [signedAreas addObject:@(signedArea)];
    }
    if (paths.count == 0) {
        return NULL;
    }

    NSMutableArray *parents = [NSMutableArray array];
    for (NSUInteger i = 0; i < paths.count; i++) {
        NSUInteger parent = NearestContainingParent(i, paths, areas);
        [parents addObject:parent == NSNotFound ? [NSNull null] : @(parent)];
    }

    NSMutableArray *rootIndexes = [NSMutableArray array];
    NSMutableArray *firstLevelIndexes = [NSMutableArray array];
    for (NSUInteger i = 0; i < paths.count; i++) {
        id parent = parents[i];
        if (parent == [NSNull null]) {
            [rootIndexes addObject:@(i)];
        }
    }
    for (NSUInteger i = 0; i < paths.count; i++) {
        id parent = parents[i];
        if (parent != [NSNull null] && [rootIndexes containsObject:parent]) {
            [firstLevelIndexes addObject:@(i)];
        }
    }

    NSMutableSet *separatorIndexes = [NSMutableSet set];
    for (NSNumber *indexNumber in firstLevelIndexes) {
        NSUInteger index = indexNumber.unsignedIntegerValue;
        CGPathRef container = (__bridge CGPathRef)paths[index];
        BOOL indexNegative = [signedAreas[index] doubleValue] < 0;
        double indexArea = [areas[index] doubleValue];
        for (NSUInteger other = 0; other < paths.count; other++) {
            if (other == index) {
                continue;
            }
            BOOL otherNegative = [signedAreas[other] doubleValue] < 0;
            if (otherNegative != indexNegative || [areas[other] doubleValue] >= indexArea) {
                continue;
            }
            if (PathStronglyContainsPath(container, (__bridge CGPathRef)paths[other])) {
                [separatorIndexes addObject:indexNumber];
                break;
            }
        }
    }

    NSMutableSet *overlayIndexes = [NSMutableSet set];
    for (NSUInteger i = 0; i < paths.count; i++) {
        id parent = parents[i];
        if (parent != [NSNull null] && [separatorIndexes containsObject:parent]) {
            [overlayIndexes addObject:@(i)];
        }
    }

    NSMutableArray *structuralCutouts = [NSMutableArray array];
    NSMutableArray *overlayCutouts = [NSMutableArray array];
    NSMutableArray *separatorPaths = [NSMutableArray array];
    NSMutableArray *rootPaths = [NSMutableArray array];
    for (NSNumber *indexNumber in rootIndexes) {
        [rootPaths addObject:paths[indexNumber.unsignedIntegerValue]];
    }
    for (NSNumber *indexNumber in firstLevelIndexes) {
        if ([separatorIndexes containsObject:indexNumber]) {
            [separatorPaths addObject:paths[indexNumber.unsignedIntegerValue]];
        } else {
            [structuralCutouts addObject:paths[indexNumber.unsignedIntegerValue]];
        }
    }
    for (NSNumber *indexNumber in overlayIndexes) {
        [overlayCutouts addObject:paths[indexNumber.unsignedIntegerValue]];
    }

    NSMutableArray *pieces = [NSMutableArray array];
    CGPathRef rootUnion = CopyUnionOfPaths(rootPaths);
    if (rootUnion) {
        NSMutableArray *rootMasks = [NSMutableArray arrayWithArray:structuralCutouts];
        [rootMasks addObjectsFromArray:overlayCutouts];
        CGPathRef rootPiece = CopySubtractUnion(rootUnion, rootMasks);
        if (rootPiece) {
            [pieces addObject:CFBridgingRelease(rootPiece)];
        }
        CGPathRelease(rootUnion);
    }

    for (id separatorObject in separatorPaths) {
        CGPathRef separator = (__bridge CGPathRef)separatorObject;
        CGPathRef separatorPiece = CopySubtractUnion(separator, overlayCutouts);
        if (separatorPiece) {
            [pieces addObject:CFBridgingRelease(separatorPiece)];
        }
    }

    return CopyUnionOfPaths(pieces);
}

static id MsgSendId(id target, SEL selector) {
    return ((id (*)(id, SEL))objc_msgSend)(target, selector);
}

static id MsgSendIdId(id target, SEL selector, id arg) {
    return ((id (*)(id, SEL, id))objc_msgSend)(target, selector, arg);
}

static id MsgSendIdDoubleDouble(id target, SEL selector, double arg1, double arg2) {
    return ((id (*)(id, SEL, double, double))objc_msgSend)(target, selector, arg1, arg2);
}

static id MsgSendIdURL(id target, SEL selector, id url, id error) {
    return ((id (*)(id, SEL, id, id))objc_msgSend)(target, selector, url, error);
}

static CGPathRef MsgSendCGPath(id target, SEL selector) {
    return ((CGPathRef (*)(id, SEL))objc_msgSend)(target, selector);
}

static NSUInteger MsgSendNSUInteger(id target, SEL selector) {
    return ((NSUInteger (*)(id, SEL))objc_msgSend)(target, selector);
}

static BOOL MsgSendBOOL(id target, SEL selector) {
    return ((BOOL (*)(id, SEL))objc_msgSend)(target, selector);
}

static double MsgSendDouble(id target, SEL selector) {
    return ((double (*)(id, SEL))objc_msgSend)(target, selector);
}

static CGRect MsgSendCGRect(id target, SEL selector) {
    return ((CGRect (*)(id, SEL))objc_msgSend)(target, selector);
}

static CGSize MsgSendCGSize(id target, SEL selector) {
    return ((CGSize (*)(id, SEL))objc_msgSend)(target, selector);
}

static NSDictionary *JSONRect(CGRect rect) {
    return @{
        @"x": @(rect.origin.x),
        @"y": @(rect.origin.y),
        @"width": @(rect.size.width),
        @"height": @(rect.size.height)
    };
}

static NSDictionary *JSONSize(CGSize size) {
    return @{
        @"width": @(size.width),
        @"height": @(size.height)
    };
}

static NSDictionary *JSONLayer(NSString *name,
                               NSUInteger index,
                               BOOL isEraser,
                               double opacity,
                               double strokeWidth,
                               CGPathRef path,
                               BOOL booleanResolved) {
    NSMutableArray *elements = [NSMutableArray array];
    CGPathApply(path, (__bridge void *)elements, CollectElement);
    CGRect box = CGPathGetPathBoundingBox(path);
    NSMutableDictionary *payload = [@{
        @"name": name ?: @"",
        @"index": @(index),
        @"is_eraser": @(isEraser),
        @"opacity": @(opacity),
        @"stroke_width": @(strokeWidth),
        @"bounds": @{
            @"x": @(box.origin.x),
            @"y": @(box.origin.y),
            @"width": @(box.size.width),
            @"height": @(box.size.height)
        },
        @"elements": elements
    } mutableCopy];
    if (booleanResolved) {
        payload[@"boolean_resolved"] = @YES;
    }
    return payload;
}

static BOOL InfoIsZeroOpacityEraser(NSDictionary *info) {
    return [info[@"is_eraser"] boolValue] && [info[@"opacity"] doubleValue] == 0;
}

static BOOL HasLaterVisibleLayer(NSArray<NSDictionary *> *infos, NSUInteger startIndex) {
    for (NSUInteger i = startIndex + 1; i < infos.count; i++) {
        if (!InfoIsZeroOpacityEraser(infos[i])) {
            return YES;
        }
    }
    return NO;
}

static NSArray<NSDictionary *> *ResolveEraserLayers(NSArray<NSDictionary *> *infos, CGRect *totalBox) {
    NSMutableArray *resolvedPaths = [NSMutableArray array];
    NSMutableArray *resolvedInfos = [NSMutableArray array];

    for (NSUInteger i = 0; i < infos.count; i++) {
        NSDictionary *info = infos[i];
        CGPathRef path = (__bridge CGPathRef)info[@"path"];
        if (!path) {
            continue;
        }

        if (InfoIsZeroOpacityEraser(info) && resolvedPaths.count > 0) {
            BOOL hasLaterVisible = HasLaterVisibleLayer(infos, i);
            NSUInteger startIndex = hasLaterVisible ? 0 : resolvedPaths.count - 1;
            for (NSUInteger pathIndex = startIndex; pathIndex < resolvedPaths.count; pathIndex++) {
                CGPathRef current = (__bridge CGPathRef)resolvedPaths[pathIndex];
                CGPathRef subtracted = CGPathCreateCopyBySubtractingPath(current, path, true);
                if (subtracted) {
                    resolvedPaths[pathIndex] = CFBridgingRelease(subtracted);
                }
            }
            continue;
        }

        if (InfoIsZeroOpacityEraser(info)) {
            continue;
        }

        CGPathRef copy = CGPathCreateCopy(path);
        if (copy) {
            [resolvedPaths addObject:CFBridgingRelease(copy)];
            [resolvedInfos addObject:info];
        }
    }

    NSMutableArray *jsonLayers = [NSMutableArray array];
    CGRect box = CGRectNull;
    for (NSUInteger i = 0; i < resolvedPaths.count; i++) {
        CGPathRef path = (__bridge CGPathRef)resolvedPaths[i];
        if (!path || PathElementCount(path) == 0) {
            continue;
        }
        NSDictionary *info = resolvedInfos[i];
        CGRect pathBox = CGPathGetPathBoundingBox(path);
        box = CGRectIsNull(box) ? pathBox : CGRectUnion(box, pathBox);
        [jsonLayers addObject:JSONLayer(
            info[@"name"],
            [info[@"index"] unsignedIntegerValue],
            NO,
            [info[@"opacity"] doubleValue],
            [info[@"stroke_width"] doubleValue],
            path,
            YES
        )];
    }

    if (totalBox && !CGRectIsNull(box)) {
        *totalBox = box;
    }
    return jsonLayers;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 5 || argc > 8 || strcmp(argv[3], "--symbols-file") != 0) {
            fprintf(stderr, "Usage: layered_vector_export <Assets.car> <out-dir> --symbols-file <symbols.txt> [--weight <continuous-weight>] [--no-shape-group-subpaths]\n");
            return 2;
        }

        NSString *assetsPath = [NSString stringWithUTF8String:argv[1]];
        NSString *outputPath = [NSString stringWithUTF8String:argv[2]];
        NSArray<NSString *> *symbols = ReadSymbolsFile([NSString stringWithUTF8String:argv[4]]);
        BOOL hasWeight = NO;
        BOOL allowShapeGroupSubpaths = YES;
        double requestedWeight = 0.0;
        for (int i = 5; i < argc; i++) {
            if (strcmp(argv[i], "--weight") == 0 && i + 1 < argc) {
                hasWeight = YES;
                requestedWeight = atof(argv[i + 1]);
                i++;
            } else if (strcmp(argv[i], "--no-shape-group-subpaths") == 0) {
                allowShapeGroupSubpaths = NO;
            } else {
                fprintf(stderr, "Unknown argument: %s\n", argv[i]);
                return 2;
            }
        }
        if (!symbols) {
            fprintf(stderr, "Could not read symbols file\n");
            return 1;
        }

        [[NSFileManager defaultManager] createDirectoryAtPath:outputPath withIntermediateDirectories:YES attributes:nil error:nil];

        Class catalogClass = NSClassFromString(@"CUICatalog");
        id catalog = MsgSendIdURL(MsgSendId(catalogClass, @selector(alloc)),
                                  NSSelectorFromString(@"initWithURL:error:"),
                                  [NSURL fileURLWithPath:assetsPath],
                                  nil);
        if (!catalog) {
            fprintf(stderr, "Could not open catalog\n");
            return 1;
        }

        for (NSString *symbol in symbols) {
            id glyph = MsgSendIdId(catalog, NSSelectorFromString(@"NS_namedVectorGlyphWithName:"), symbol);
            if (!glyph) {
                fprintf(stderr, "Missing glyph: %s\n", symbol.UTF8String);
                continue;
            }
            if (hasWeight) {
                double continuousSize = MsgSendDouble(glyph, NSSelectorFromString(@"glyphContinuousSize"));
                glyph = MsgSendIdDoubleDouble(glyph, NSSelectorFromString(@"copyWithContinuousWeight:continuousSize:"), requestedWeight, continuousSize);
            }

            NSArray *layers = MsgSendId(glyph, NSSelectorFromString(@"monochromeLayers"));
            NSArray *shapeGroupSubpaths = MsgSendId(glyph, NSSelectorFromString(@"_createShapeGroupSubpaths"));
            NSMutableArray *jsonLayers = [NSMutableArray array];
            NSMutableArray *nativeLayerInfos = [NSMutableArray array];
            CGRect totalBox = CGRectNull;
            NSUInteger exportedElementCount = 0;
            NSUInteger skippedLayerCount = 0;
            CGRect contentBounds = MsgSendCGRect(glyph, NSSelectorFromString(@"contentBounds"));
            CGRect alignmentRect = MsgSendCGRect(glyph, NSSelectorFromString(@"alignmentRect"));
            CGRect interiorAlignmentRect = MsgSendCGRect(glyph, NSSelectorFromString(@"interiorAlignmentRect"));
            CGSize referenceCanvasSize = MsgSendCGSize(glyph, NSSelectorFromString(@"referenceCanvasSize"));
            BOOL usedShapeGroupLayer = NO;
            for (id layer in layers) {
                CGPathRef path = MsgSendCGPath(layer, NSSelectorFromString(@"shape"));
                if (!path) {
                    skippedLayerCount++;
                    continue;
                }
                NSMutableArray *elements = [NSMutableArray array];
                CGPathApply(path, (__bridge void *)elements, CollectElement);
                exportedElementCount += elements.count;
                CGRect box = CGPathGetPathBoundingBox(path);
                totalBox = CGRectIsNull(totalBox) ? box : CGRectUnion(totalBox, box);
                id name = MsgSendId(layer, NSSelectorFromString(@"name"));
                NSUInteger index = MsgSendNSUInteger(layer, NSSelectorFromString(@"index"));
                BOOL isEraser = MsgSendBOOL(layer, NSSelectorFromString(@"isEraserLayer"));
                double opacity = MsgSendDouble(layer, NSSelectorFromString(@"opacity"));
                double strokeWidth = MsgSendDouble(layer, NSSelectorFromString(@"strokeWidth"));
                [jsonLayers addObject:JSONLayer(name, index, isEraser, opacity, strokeWidth, path, NO)];
                CGPathRef retainedPath = CGPathCreateCopy(path);
                if (retainedPath) {
                    [nativeLayerInfos addObject:@{
                        @"name": name ?: @"",
                        @"index": @(index),
                        @"is_eraser": @(isEraser),
                        @"opacity": @(opacity),
                        @"stroke_width": @(strokeWidth),
                        @"path": CFBridgingRelease(retainedPath)
                    }];
                }
            }

            BOOL hasZeroOpacityEraser = NO;
            for (NSDictionary *info in nativeLayerInfos) {
                if (InfoIsZeroOpacityEraser(info)) {
                    hasZeroOpacityEraser = YES;
                    break;
                }
            }
            if (hasZeroOpacityEraser) {
                NSArray *resolvedLayers = ResolveEraserLayers(nativeLayerInfos, &totalBox);
                if (resolvedLayers.count > 0) {
                    jsonLayers = [resolvedLayers mutableCopy];
                    usedShapeGroupLayer = YES;
                }
            }

            NSUInteger shapeGroupElementCount = 0;
            if (!usedShapeGroupLayer && allowShapeGroupSubpaths && skippedLayerCount > 0 && [shapeGroupSubpaths respondsToSelector:@selector(count)]) {
                for (id pathObject in shapeGroupSubpaths) {
                    CGPathRef path = MsgSendCGPath(pathObject, NSSelectorFromString(@"path"));
                    shapeGroupElementCount += PathElementCount(path);
                }
            }
            if (!usedShapeGroupLayer && allowShapeGroupSubpaths && skippedLayerCount > 0 && shapeGroupElementCount > exportedElementCount) {
                NSMutableArray *pathGroups = [NSMutableArray array];
                for (id pathObject in shapeGroupSubpaths) {
                    CGPathRef path = MsgSendCGPath(pathObject, NSSelectorFromString(@"path"));
                    if (!path) {
                        continue;
                    }
                    NSMutableArray *groupElements = [NSMutableArray array];
                    CGPathApply(path, (__bridge void *)groupElements, CollectElement);
                    [pathGroups addObject:groupElements];
                }
                CGPathRef booleanPath = CopyShapeGroupPaintFlattenedPath(shapeGroupSubpaths);
                if (!booleanPath) {
                    booleanPath = CopyShapeGroupDominantSubtractPath(shapeGroupSubpaths);
                }
                if (!booleanPath) {
                    booleanPath = CopyShapeGroupBooleanPath(shapeGroupSubpaths);
                }
                NSMutableArray *elements = [NSMutableArray array];
                if (booleanPath) {
                    CGPathApply(booleanPath, (__bridge void *)elements, CollectElement);
                }
                if (booleanPath && elements.count > 0) {
                    [jsonLayers removeAllObjects];
                    totalBox = CGPathGetPathBoundingBox(booleanPath);
                    usedShapeGroupLayer = YES;
                    [jsonLayers addObject:@{
                        @"name": @"shape-group-subpaths",
                        @"index": @0,
                        @"is_eraser": @NO,
                        @"opacity": @1,
                        @"stroke_width": @0,
                        @"bounds": @{
                            @"x": @(totalBox.origin.x),
                            @"y": @(totalBox.origin.y),
                            @"width": @(totalBox.size.width),
                            @"height": @(totalBox.size.height)
                        },
                        @"boolean_resolved": @YES,
                        @"elements": elements,
                        @"path_groups": pathGroups
                    }];
                }
                if (booleanPath) {
                    CGPathRelease(booleanPath);
                }
            }

            NSDictionary *payload = @{
                @"symbol": symbol,
                @"source": usedShapeGroupLayer ? @"CUINamedVectorGlyph._createShapeGroupSubpaths" : @"CUINamedVectorGlyph.monochromeLayers.shape",
                @"continuous_weight": @(hasWeight ? requestedWeight : MsgSendDouble(glyph, NSSelectorFromString(@"glyphContinuousWeight"))),
                @"continuous_size": @(MsgSendDouble(glyph, NSSelectorFromString(@"glyphContinuousSize"))),
                @"bounds": JSONRect(totalBox),
                @"content_bounds": JSONRect(contentBounds),
                @"alignment_rect": JSONRect(alignmentRect),
                @"interior_alignment_rect": JSONRect(interiorAlignmentRect),
                @"reference_canvas_size": JSONSize(referenceCanvasSize),
                @"layers": jsonLayers
            };
            NSString *safe = [symbol stringByReplacingOccurrencesOfString:@"/" withString:@"_"];
            NSString *file = [outputPath stringByAppendingPathComponent:[safe stringByAppendingString:@".json"]];
            [JSONString(payload) writeToFile:file atomically:YES encoding:NSUTF8StringEncoding error:nil];
            printf("%s: %lu layers\n", symbol.UTF8String, (unsigned long)jsonLayers.count);
        }
        return 0;
    }
}
