export function subtitlePreviewLayout({ containerWidth, containerHeight, videoWidth, videoHeight, fontSize = 18, offsetY = 0 }) {
  if (!(containerWidth > 0 && containerHeight > 0 && videoWidth > 0 && videoHeight > 0)) {
    return { imageWidth: 0, bottom: 0, fontSize: 18, lineGap: 4 };
  }
  const imageWidth = Math.min(containerWidth, containerHeight * videoWidth / videoHeight);
  const imageHeight = imageWidth * videoHeight / videoWidth;
  const letterboxBottom = (containerHeight - imageHeight) / 2;
  const scaleX = imageWidth / 640;
  return {
    imageWidth,
    bottom: letterboxBottom + 80 * imageHeight / videoHeight + offsetY * scaleX,
    fontSize: Math.max(10 * imageWidth / videoWidth, Math.floor(fontSize * videoWidth / 640) * imageWidth / videoWidth),
    lineGap: 10 * imageWidth / videoWidth,
  };
}
