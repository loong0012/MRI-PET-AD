/**
 * 图形验证码
 * - Mock 模式：本地生成并校验
 * - 真实后端模式：后端 /api/auth/captcha 下发服务端渲染的 PNG（data URL），
 *   明文 code 不离开服务端，前端仅将图片绘制到 canvas 供用户识别
 */
const CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
const CAPTCHA_TTL = 5 * 60 * 1000

interface CaptchaEntry {
  code: string
  expire: number
}

const captchaStore = new Map<string, CaptchaEntry>()
let seq = 0

/**
 * 在 canvas 上绘制指定验证码（通用绘制逻辑）
 * code: 4 位验证码字符串
 */
function drawCode(canvas: HTMLCanvasElement, code: string): void {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const w = canvas.width
  const h = canvas.height

  // 背景：医疗蓝灰浅底
  ctx.fillStyle = '#eef3f8'
  ctx.fillRect(0, 0, w, h)

  // 干扰线
  for (let i = 0; i < 4; i++) {
    ctx.strokeStyle = `rgba(47,109,163,${0.15 + Math.random() * 0.2})`
    ctx.beginPath()
    ctx.moveTo(Math.random() * w, Math.random() * h)
    ctx.lineTo(Math.random() * w, Math.random() * h)
    ctx.stroke()
  }

  // 逐字符绘制（随机旋转/偏移）
  for (let i = 0; i < code.length; i++) {
    ctx.save()
    ctx.font = `600 ${h * 0.58}px Consolas, 'Courier New', monospace`
    ctx.fillStyle = ['#2f6da3', '#3c556b', '#51749a', '#28425c'][i % 4]
    ctx.translate((w / 4) * i + w / 9, h / 2 + h * 0.2)
    ctx.rotate((Math.random() - 0.5) * 0.5)
    ctx.fillText(code[i], 0, 0)
    ctx.restore()
  }

  // 干扰点
  for (let i = 0; i < 26; i++) {
    ctx.fillStyle = 'rgba(60,85,107,.35)'
    ctx.fillRect(Math.random() * w, Math.random() * h, 1.6, 1.6)
  }
}

/** Mock 模式：本地生成验证码并绘制，返回 captchaKey */
export function drawCaptcha(canvas: HTMLCanvasElement): string {
  // 生成 4 位随机码（剔除易混淆字符）
  let code = ''
  for (let i = 0; i < 4; i++) {
    code += CHARS[Math.floor(Math.random() * CHARS.length)]
  }

  drawCode(canvas, code)

  const key = `cap_${Date.now()}_${seq++}`
  captchaStore.set(key, { code: code.toLowerCase(), expire: Date.now() + CAPTCHA_TTL })
  // 顺手清理过期项
  for (const [k, v] of captchaStore) {
    if (v.expire < Date.now()) captchaStore.delete(k)
  }
  return key
}

/**
 * 真实后端模式：将后端渲染的验证码 PNG（data URL）绘制到 canvas。
 * 图片尺寸 120×44，canvas 内部坐标按其固有宽高绘制，CSS 负责显示尺寸。
 */
export function drawCaptchaImage(canvas: HTMLCanvasElement, dataUrl: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      canvas.width = img.width
      canvas.height = img.height
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('canvas 不可用'))
        return
      }
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      ctx.drawImage(img, 0, 0)
      resolve()
    }
    img.onerror = () => reject(new Error('验证码图片加载失败'))
    img.src = dataUrl
  })
}

/** Mock 模式校验验证码（忽略大小写，一次性使用） */
export function verifyCaptcha(key: string, input: string): boolean {
  const entry = captchaStore.get(key)
  if (!entry) return false
  captchaStore.delete(key)
  return entry.expire > Date.now() && entry.code === input.trim().toLowerCase()
}
