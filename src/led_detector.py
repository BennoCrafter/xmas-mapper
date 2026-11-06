import argparse

import cv2
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("-i", "--image", required=True, help="path to the image file")
args = vars(ap.parse_args())

image = cv2.imread(args["image"])

if image is None:
    raise ValueError("Bild konnte nicht geladen werden. Bitte Pfad überprüfen.")

# grey scale and Gaussian Blur
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
blurred = cv2.GaussianBlur(gray, (11, 11), 0)

# Threshold
thresh = cv2.threshold(blurred, 200, 255, cv2.THRESH_BINARY)[1]


thresh = cv2.erode(thresh, None, iterations=2)
thresh = cv2.dilate(thresh, None, iterations=4)

numLabels, labels, stats, centroids = cv2.connectedComponentsWithStats(thresh, connectivity=8)

mask = np.zeros(thresh.shape, dtype="uint8")

for label in range(1, numLabels):
    numPixels = stats[label, cv2.CC_STAT_AREA]

    if numPixels > 200:
        mask[labels == label] = 255

cv2.imshow("original", image)
cv2.imshow("threshold", thresh)
cv2.imshow("mask", mask)

cv2.waitKey(0)
cv2.destroyAllWindows()
