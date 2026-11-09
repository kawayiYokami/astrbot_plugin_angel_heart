/**
 * 字节数格式化成 MB，保留一位小数。
 *
 * 只做格式化，不掺业务文案：显示成「未占用空间」还是「0 MB」由调用方按场景决定。
 */
export function formatMB(size: number): string {
  if (!size || size <= 0) return '0 MB'
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}
