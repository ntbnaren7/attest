/** Shrink a phone photo to ≤1600px JPEG so it uploads fast and stays under the server cap. PDFs pass through. */
export async function shrink(file: File): Promise<Blob> {
  if (file.type === 'application/pdf') return file
  const bmp = await createImageBitmap(file)
  const k = Math.min(1, 1600 / Math.max(bmp.width, bmp.height))
  const c = document.createElement('canvas')
  c.width = Math.round(bmp.width * k)
  c.height = Math.round(bmp.height * k)
  c.getContext('2d')!.drawImage(bmp, 0, 0, c.width, c.height)
  return new Promise((res) => c.toBlob((b) => res(b ?? file), 'image/jpeg', 0.85))
}
