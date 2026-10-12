"""Isoler la pochette au premier plan avant la lecture automatique."""

def cover_pixels(image):
    import numpy as np
    import cv2
    pixels = np.asarray(image.convert('RGB'))
    h, w = pixels.shape[:2]
    scale = min(1, 900 / max(h, w))
    small = cv2.resize(pixels, (int(w * scale), int(h * scale)))
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 35, 110)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour) / (small.shape[0] * small.shape[1])
        if not .25 < area < .78:
            continue
        quad = cv2.approxPolyDP(contour, .025 * cv2.arcLength(contour, True), True)
        if len(quad) != 4 or not cv2.isContourConvex(quad):
            continue
        x, y, cw, ch = cv2.boundingRect(quad)
        if not .8 < cw / ch < 1.25:
            continue
        candidates.append((area, quad.reshape(4, 2).astype(np.float32) / scale))
    if not candidates:
        return pixels
    points = max(candidates, key=lambda x: x[0])[1]
    ordered = np.empty((4, 2), np.float32)
    sums = points.sum(axis=1); differences = np.diff(points, axis=1).ravel()
    ordered[0] = points[np.argmin(sums)]; ordered[2] = points[np.argmax(sums)]
    ordered[1] = points[np.argmin(differences)]; ordered[3] = points[np.argmax(differences)]
    if len(np.unique(ordered, axis=0)) != 4:
        return pixels
    size = int(max(np.linalg.norm(ordered[i] - ordered[(i+1) % 4]) for i in range(4)))
    size = max(600, min(size, 1600))
    target = np.float32([[0, 0], [size-1, 0], [size-1, size-1], [0, size-1]])
    return cv2.warpPerspective(pixels, cv2.getPerspectiveTransform(ordered, target), (size, size))

def useful_region(region):
    text = str(region.get('text', ''))
    import re
    return bool(re.search(r'[^\W\d_]', text)) and text.strip().casefold() not in {'vente', 'finale', 'sale', 'price'}
