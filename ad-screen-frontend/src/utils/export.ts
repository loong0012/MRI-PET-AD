/**
 * 纯前端 CSV 导出（用于推理日志 / 审计日志导出）
 * \uFEFF BOM 头保证 Excel 打开中文不乱码
 */
export function downloadCsv(filename: string, headers: string[], rows: (string | number)[][]): void {
  const escape = (v: string | number): string => {
    let s = String(v)
    // 公式注入防护：= + - @ 及 Tab/CR 开头的单元格会被 Excel 当公式执行，
    // 统一前置单引号（与后端 services/utils.csv_sanitize 同口径）
    if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [headers.map(escape).join(','), ...rows.map((r) => r.map(escape).join(','))]
  const blob = new Blob(['\uFEFF' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  // 部分 Firefox 要求节点挂载到 DOM 后 click() 才触发下载
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/**
 * 打印指定 DOM 区域（PDF 导出 = 浏览器打印对话框中选择"另存为 PDF"）
 * print-area 类已在全局样式中定义 @media print 规则
 */
export function printSection(): void {
  window.print()
}

/**
 * 将指定 DOM 元素导出为 PDF 文件（html2canvas + jsPDF，A4 纵向）
 * 无需打开打印对话框，一键生成可下载的 PDF。
 * @param el 目标 DOM 元素（报告 A4 预览区）
 * @param filename 导出文件名（不含扩展名）
 */
export async function exportElementToPdf(el: HTMLElement, filename: string): Promise<void> {
  const html2canvas = (await import('html2canvas')).default
  const { jsPDF } = await import('jspdf')

  // 按 A4 比例渲染（210mm × 297mm），提高清晰度
  const canvas = await html2canvas(el, {
    scale: 2,
    useCORS: true,
    backgroundColor: '#ffffff',
    logging: false
  })
  const imgData = canvas.toDataURL('image/jpeg', 0.95)
  // A4 纵向尺寸（mm）
  const pdf = new jsPDF('p', 'mm', 'a4')
  const pageW = pdf.internal.pageSize.getWidth()
  const pageH = pdf.internal.pageSize.getHeight()
  const imgW = pageW
  const imgH = (canvas.height * pageW) / canvas.width
  // 单页适配：内容不超过一页直接铺满
  if (imgH <= pageH) {
    pdf.addImage(imgData, 'JPEG', 0, 0, imgW, imgH)
  } else {
    // 超长内容分页（按 A4 高度切分）
    let heightLeft = imgH
    let position = 0
    pdf.addImage(imgData, 'JPEG', 0, position, imgW, imgH)
    heightLeft -= pageH
    while (heightLeft > 0) {
      position = heightLeft - imgH
      pdf.addPage()
      pdf.addImage(imgData, 'JPEG', 0, position, imgW, imgH)
      heightLeft -= pageH
    }
  }
  pdf.save(`${filename}.pdf`)
}
