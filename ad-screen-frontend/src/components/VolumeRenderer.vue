<template>
  <div class="vol-renderer">
    <div ref="canvasBox" class="vol-canvas-box"></div>

    <!-- 加载/失败遮罩 -->
    <div v-if="loading" class="vol-mask">
      <el-icon class="is-loading" :size="28"><Loading /></el-icon>
      <span class="ml-2 text-sm text-slate-300">正在加载 3D 体数据…</span>
    </div>
    <div v-else-if="errorMsg" class="vol-mask flex-col">
      <el-icon :size="28" class="text-red-400"><WarningFilled /></el-icon>
      <span class="mt-2 text-sm text-red-300">{{ errorMsg }}</span>
    </div>

    <!-- 控制条 -->
    <div class="vol-toolbar">
      <el-radio-group v-model="modality" size="small" @change="reload">
        <el-radio-button value="MRI">MRI</el-radio-button>
        <el-radio-button value="PET">PET</el-radio-button>
        <el-radio-button value="FUSION">融合</el-radio-button>
      </el-radio-group>
      <div class="flex items-center gap-2 ml-3">
        <span class="text-xs text-slate-300 whitespace-nowrap">阈值</span>
        <el-slider v-model="threshold" :min="0.02" :max="0.6" :step="0.01" class="w-28" />
      </div>
      <el-tooltip :content="showAnnotations ? '隐藏脑区标注' : '显示重点脑区标注'" placement="top" :show-after="300">
        <button
          class="ml-2 w-7 h-7 rounded-md flex items-center justify-center"
          :class="showAnnotations ? 'bg-[#7E57C2] text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'"
          @click="toggleAnnotations"
        >
          <el-icon :size="14"><Location /></el-icon>
        </button>
      </el-tooltip>
      <el-tooltip content="自动旋转" placement="top" :show-after="300">
        <button
          class="ml-1 w-7 h-7 rounded-md flex items-center justify-center"
          :class="autoRotate ? 'bg-[#4FC3F7] text-white' : 'bg-black/45 text-white/85 hover:bg-black/70'"
          @click="autoRotate = !autoRotate"
        >
          <el-icon :size="14"><RefreshRight /></el-icon>
        </button>
      </el-tooltip>
    </div>

    <!-- 剖切平面：暴露海马等深部结构 -->
    <div class="vol-clipbar">
      <span class="text-xs text-slate-300 whitespace-nowrap mr-1">剖切</span>
      <el-radio-group v-model="clipMode" size="small" @change="applyClip">
        <el-radio-button value="none">完整</el-radio-button>
        <el-radio-button value="sagittal">矢状切</el-radio-button>
        <el-radio-button value="coronal">冠状切</el-radio-button>
        <el-radio-button value="axial">轴位切</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 重点脑区图例（AI 异常标红；点击定位脑区） -->
    <div v-if="showAnnotations" class="vol-legend">
      <div class="text-[10px] text-white/40 mb-1">点击脑区名可定位查看（图谱近似 · 示教用途）</div>
      <div
        v-for="r in regions"
        :key="`${r.region.key}_${r.pos.join()}`"
        class="flex items-center gap-1.5 leading-5 cursor-pointer rounded px-0.5 hover:bg-white/10"
        @click="focusRegion(r)"
      >
        <span
          class="w-2 h-2 rounded-full shrink-0"
          :style="{ background: isAbnormal(r.region.name) ? '#EF5350' : r.region.color, outline: isAbnormal(r.region.name) ? '1.5px solid #EF5350' : 'none' }"
        />
        <span class="text-[11px]" :class="isAbnormal(r.region.name) ? 'text-[#EF5350] font-medium' : 'text-white/70'">{{ r.region.name }}</span>
        <span v-if="isAbnormal(r.region.name)" class="text-[9.5px] text-[#EF5350]">⚠ AI 异常</span>
      </div>
    </div>
    <!-- 融合模式代谢图例 -->
    <div v-if="modality === 'FUSION'" class="vol-fusion-legend">
      <span class="dot" style="background: #5270ff" />低代谢区（AD 受累）
      <span class="dot" style="background: #ff6b1f" />高摄取区
      <span class="dot" style="background: #cfd8dc" />MRI 结构
    </div>
    <div class="vol-hint">拖拽旋转 · 滚轮缩放 · 点击彩点定位脑区</div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { Loading, WarningFilled, RefreshRight, Location } from '@element-plus/icons-vue'
import { loadVolume3D } from '@/api/imaging'
import { getRegion3DPositions, type BrainRegion } from '@/utils/brainRegions'
import type { HeatmapRegion } from '@/utils/imaging'

const props = defineProps<{
  caseId: string
  initialModality?: 'MRI' | 'PET'
  /** AI 异常脑区列表（匹配的重点脑区在 3D 中红色警示标注） */
  abnormalRegions?: HeatmapRegion[]
}>()

const canvasBox = ref<HTMLDivElement | null>(null)
const loading = ref(false)
const errorMsg = ref('')
type Mode = 'MRI' | 'PET' | 'FUSION'
const modality = ref<Mode>(props.initialModality ?? 'MRI')
const threshold = ref(0.12)
const autoRotate = ref(true)
const showAnnotations = ref(true)
const regions = getRegion3DPositions()
/** 三平面剖切（世界空间：x=左右 i, y=上下 k, z=前后 -j；
 * 矢状切去患者右侧、冠状切去额侧(z<0)、轴位切去颅顶） */
type ClipMode = 'none' | 'sagittal' | 'coronal' | 'axial'
const clipMode = ref<ClipMode>('none')
const CLIP_DIRS: Record<ClipMode, [number, number, number]> = {
  none: [0, 0, 0],
  sagittal: [1, 0, 0],
  coronal: [0, 0, -1],
  axial: [0, 1, 0]
}

/** 异常脑区名集合 */
function isAbnormal(name: string): boolean {
  return (props.abnormalRegions ?? []).some((r) => r.region === name)
}

let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let controls: OrbitControls | null = null
let mesh: THREE.Mesh | null = null
let material: THREE.ShaderMaterial | null = null
let textureMri: THREE.Data3DTexture | null = null
let texturePet: THREE.Data3DTexture | null = null
let annotationGroup: THREE.Group | null = null
let orientationSprites: THREE.Sprite[] = []
let rafId = 0
let resizeObs: ResizeObserver | null = null
let raycaster: THREE.Raycaster | null = null
/** 标注引用（点击拾取 / 相机定位） */
let annRefs: Array<{ region: BrainRegion; dot: THREE.Mesh; sprite: THREE.Sprite; pos: THREE.Vector3 }> = []
/** 标签显示集合：默认仅显示 AI 异常脑区标签，避免全部标签互相遮挡 */
const labelVisible = ref<Set<string>>(new Set())

const VERT = /* glsl */ `
  varying vec3 vPos;
  void main() {
    vPos = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`

const FRAG = /* glsl */ `
  precision highp sampler3D;
  uniform sampler3D uTexMri;
  uniform sampler3D uTexPet;
  uniform float uThreshold;
  uniform int uMode;   // 0 = MRI 灰度, 1 = PET 伪彩, 2 = MRI-PET 融合
  uniform vec3 uCamPos;
  uniform vec3 uClipDir;  // 剖切平面法向（体空间 i,j,k 轴），0 表示不剖切
  uniform float uClipPos; // 剖切位置（沿法向的有符号距离，中点=0）
  varying vec3 vPos;

  // 与单位立方体 [−0.5, 0.5]^3 求交，返回 (tNear, tFar)
  vec2 intersectBox(vec3 origin, vec3 dir) {
    vec3 boxMin = vec3(-0.5);
    vec3 boxMax = vec3(0.5);
    vec3 invD = 1.0 / dir;
    vec3 tbot = invD * (boxMin - origin);
    vec3 ttop = invD * (boxMax - origin);
    vec3 tmin = min(ttop, tbot);
    vec3 tmax = max(ttop, tbot);
    float tNear = max(max(tmin.x, tmin.y), tmin.z);
    float tFar = min(min(tmax.x, tmax.y), tmax.z);
    return vec2(tNear, tFar);
  }

  vec3 cmapGray(float t) {
    // MRI：黑→蓝灰→白（骨窗风格）
    vec3 dark = vec3(0.02, 0.03, 0.06);
    vec3 mid = vec3(0.55, 0.6, 0.68);
    vec3 hi = vec3(1.0, 1.0, 0.98);
    return t < 0.5 ? mix(dark, mid, t * 2.0) : mix(mid, hi, (t - 0.5) * 2.0);
  }

  vec3 cmapJet(float t) {
    // PET：蓝→青→绿→黄→红
    float r = clamp(1.5 - abs(4.0 * t - 3.0), 0.0, 1.0);
    float g = clamp(1.5 - abs(4.0 * t - 2.0), 0.0, 1.0);
    float b = clamp(1.5 - abs(4.0 * t - 1.0), 0.0, 1.0);
    return vec3(r, g, b);
  }

  // 起点抖动哈希（消除层状采样条带）
  float hash12(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
  }

  // 世界坐标 (x=LR i, y=IS k, z=PA -j：保证右手系、患者前方朝 z-) → 3D 纹理坐标 (i,j,k)
  vec3 w2t(vec3 u) { return vec3(u.x, 1.0 - u.z, u.y); }

  // MRI 局部表面法线（中心差分梯度）：脑沟脑回立体感的关键。
  // 世界偏移经 w2t 落到纹理轴采样，差分结果即世界空间 (di, dk, -dj)。
  vec3 mriNormal(vec3 uvw) {
    float e = 0.02;
    vec3 ei = vec3(e, 0.0, 0.0);
    vec3 ej = vec3(0.0, e, 0.0);
    vec3 ek = vec3(0.0, 0.0, e);
    float ni = texture(uTexMri, w2t(uvw + ei)).r - texture(uTexMri, w2t(uvw - ei)).r;
    float nj = texture(uTexMri, w2t(uvw + ej)).r - texture(uTexMri, w2t(uvw - ej)).r;
    float nk = texture(uTexMri, w2t(uvw + ek)).r - texture(uTexMri, w2t(uvw - ek)).r;
    return normalize(vec3(ni, nk, nj) + vec3(1e-5));
  }

  void main() {
    vec3 dir = normalize(vPos - uCamPos);
    vec2 tHit = intersectBox(uCamPos, dir);
    if (tHit.y < 0.0 || tHit.x > tHit.y) discard;
    float tStart = max(tHit.x, 0.0);
    const int STEPS = 144;
    float dt = (tHit.y - tStart) / float(STEPS);
    vec3 pos = uCamPos + dir * (tStart + dt * hash12(gl_FragCoord.xy));

    if (uMode == 1) {
      // PET：MIP 最大强度投影（临床标准视图，代谢热区一目了然）
      float maxPet = 0.0;
      for (int i = 0; i < STEPS; i++) {
        vec3 uvw0 = pos + vec3(0.5);
        if (dot(uvw0 - vec3(0.5), uClipDir) <= uClipPos) {
          float vp = texture(uTexPet, w2t(uvw0)).r;
          if (vp > maxPet) maxPet = vp;
        }
        pos += dir * dt;
      }
      float a = smoothstep(uThreshold, uThreshold + 0.25, maxPet);
      if (a < 0.02) discard;
      gl_FragColor = vec4(cmapJet(maxPet), a * 0.92);
    } else {
      // MRI 单模态 / 融合：直接体绘制（DVR）+ 梯度光照 + 逐体素代谢双色
      vec3 lightDir = normalize(vec3(0.4, 0.75, 0.55));
      vec4 acc = vec4(0.0);
      for (int i = 0; i < STEPS; i++) {
        vec3 uvw = pos + vec3(0.5);
        pos += dir * dt;
        // 剖切平面：被切除一侧的采样点跳过（暴露深部结构）
        if (dot(uvw - vec3(0.5), uClipDir) > uClipPos) continue;
        float mri = texture(uTexMri, w2t(uvw)).r;
        // 代谢着色逐体素计算（非沿射线最大值），低代谢区能被准确点亮为冷色。
        // 后端 PET 以脑内 p75 皮层摄取为参考：t=(ratio-0.3)/1.0。
        float lowM = 0.0;
        float highM = 0.0;
        if (uMode == 2) {
          float pet = texture(uTexPet, w2t(uvw)).r;
          // 64³ 下皮层与白质/CSF 存在部分容积混合：皮层门控 t>0.24 排除表层 PVE 与白质
          float cortex = smoothstep(0.24, 0.34, pet);
          // 实测分区：后扣带/楔前叶 t≈0.24、颞顶≈0.43（蓝），额叶/感觉运动≈0.5，小脑≈0.69（暖）
          lowM = (1.0 - smoothstep(0.4, 0.52, pet)) * cortex;  // 后部低代谢 → 冷色
          highM = smoothstep(0.66, 0.82, pet);                 // 保留皮层/小脑/深部核团 → 暖色
        }
        float aMri = smoothstep(uThreshold, uThreshold + 0.22, mri);
        float a = uMode == 2 ? max(aMri * 0.4, max(lowM * 0.42, highM * 0.62)) : aMri;
        if (a > 0.015) {
          // 梯度光照：让脑沟/脑回/皮层起伏可见
          vec3 nrm = mriNormal(uvw);
          float diff = clamp(dot(nrm, lightDir) * 0.5 + 0.62, 0.0, 1.15);
          float rim = pow(1.0 - abs(dot(nrm, -dir)), 2.0);
          vec3 col = cmapGray(clamp(mri * (0.5 + 0.5 * diff), 0.0, 1.0));
          col = col * (0.45 + 0.6 * diff) + rim * 0.05;
          if (uMode == 2) {
            col = mix(col, vec3(0.32, 0.44, 1.0), clamp(lowM * 0.72, 0.0, 1.0));
            col = mix(col, vec3(1.0, 0.42, 0.12), clamp(highM * 0.9, 0.0, 1.0));
          }
          // 前后合成
          acc.rgb += (1.0 - acc.a) * col * a;
          acc.a += (1.0 - acc.a) * a;
          if (acc.a > 0.97) break;
        }
      }
      if (acc.a < 0.02) discard;
      gl_FragColor = vec4(acc.rgb, acc.a);
    }
  }
`

/** 生成文字标签 Sprite（canvas 纹理，带深色底衬保证可读） */
function makeTextSprite(text: string, color: string): THREE.Sprite {
  const cnv = document.createElement('canvas')
  cnv.width = 256
  cnv.height = 64
  const c = cnv.getContext('2d')!
  c.font = '600 34px "PingFang SC", "Microsoft YaHei", sans-serif'
  c.textAlign = 'center'
  c.textBaseline = 'middle'
  // 底衬
  const tw = c.measureText(text).width + 26
  c.fillStyle = 'rgba(8,12,18,0.72)'
  c.beginPath()
  c.roundRect((256 - tw) / 2, 8, tw, 48, 10)
  c.fill()
  c.strokeStyle = color
  c.lineWidth = 3
  c.stroke()
  c.fillStyle = color
  c.fillText(text, 128, 34)
  const tex = new THREE.CanvasTexture(cnv)
  tex.colorSpace = THREE.SRGBColorSpace
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false })
  const sp = new THREE.Sprite(mat)
  sp.scale.set(0.26, 0.065, 1)
  sp.renderOrder = 10
  return sp
}

/** 构建 3D 脑区标注组：锚点小球（可点击）+ 名称标签（默认仅异常脑区显示） */
function buildAnnotations(): THREE.Group {
  const group = new THREE.Group()
  annRefs = []
  regions.forEach(({ region, pos }, idx) => {
    const abn = isAbnormal(region.name)
    const color = abn ? '#EF5350' : region.color
    const [x, y, z] = pos
    // 锚点小球（异常更大更醒目）+ 深色光晕（亮暗皮层上都可辨）
    const r = abn ? 0.015 : 0.009
    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(r, 12, 12),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(color), transparent: true, opacity: 0.95, depthTest: false })
    )
    const halo = new THREE.Mesh(
      new THREE.SphereGeometry(r * 2.1, 12, 12),
      new THREE.MeshBasicMaterial({ color: 0x0b0f14, transparent: true, opacity: 0.38, depthTest: false })
    )
    halo.renderOrder = 8
    dot.add(halo)
    dot.position.set(x, y, z)
    dot.renderOrder = 9
    dot.userData.regionKey = region.key
    group.add(dot)
    // 标签防重叠：双侧结构向同侧外移分开左右两枚；中线结构三档高度 + 左右微错位
    const cycle = idx % 3
    const sp = makeTextSprite((abn ? '⚠ ' : '') + region.name, color)
    const lateral = region.bilateral ? Math.sign(x || 1) * 0.075 : cycle === 1 ? 0.055 : cycle === 2 ? -0.055 : 0
    sp.position.set(x + lateral, y + 0.045 + (region.bilateral ? 0 : cycle * 0.04), z)
    sp.visible = labelVisible.value.has(region.key)
    group.add(sp)
    annRefs.push({ region, dot, sprite: sp, pos: new THREE.Vector3(x, y, z) })
  })
  return group
}

/** 切换某脑区标签显隐（同时尊重剖切平面显隐） */
function toggleLabel(key: string): void {
  const next = new Set(labelVisible.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  labelVisible.value = next
  applyClip()
}

/** 相机飞向脑区方向（保持当前距离，绕原点旋转定位） */
function flyTo(pos: THREE.Vector3): void {
  if (!camera || !controls) return
  const dir = pos.clone()
  if (dir.length() < 0.01) dir.set(0, 0.35, -1)
  dir.normalize()
  const dist = camera.position.length()
  camera.position.copy(dir.multiplyScalar(dist))
  controls.update()
}

/** 图例点击：显示该脑区标签并定位 */
function focusRegion(r: { region: BrainRegion; pos: [number, number, number] }): void {
  if (!labelVisible.value.has(r.region.key)) toggleLabel(r.region.key)
  flyTo(new THREE.Vector3(...r.pos))
}

/** 点击拾取：命中锚点小球 → 显示标签并定位 */
function onCanvasClick(e: MouseEvent): void {
  if (!renderer || !camera || !raycaster) return
  const rect = renderer.domElement.getBoundingClientRect()
  const ndc = new THREE.Vector2(
    ((e.clientX - rect.left) / rect.width) * 2 - 1,
    -((e.clientY - rect.top) / rect.height) * 2 + 1
  )
  raycaster.setFromCamera(ndc, camera)
  const hits = raycaster.intersectObjects(annRefs.map((r) => r.dot), false)
  if (hits.length > 0) {
    const key = hits[0].object.userData.regionKey as string
    const ref = annRefs.find((r) => r.region.key === key)
    if (ref) {
      if (!labelVisible.value.has(key)) toggleLabel(key)
      flyTo(ref.pos)
    }
  }
}

/** 悬停拾取：命中锚点 → 放大提示可点击（医患精准定位） */
let hoveredKey: string | null = null
function onPointerMove(e: MouseEvent): void {
  if (!renderer || !camera || !raycaster) return
  const rect = renderer.domElement.getBoundingClientRect()
  const ndc = new THREE.Vector2(
    ((e.clientX - rect.left) / rect.width) * 2 - 1,
    -((e.clientY - rect.top) / rect.height) * 2 + 1
  )
  raycaster.setFromCamera(ndc, camera)
  const hits = raycaster.intersectObjects(annRefs.map((r) => r.dot), false)
  const key = hits.length > 0 ? (hits[0].object.userData.regionKey as string) : null
  if (key === hoveredKey) return
  hoveredKey = key
  renderer.domElement.style.cursor = key ? 'pointer' : 'grab'
  for (const r of annRefs) {
    r.dot.scale.setScalar(r.region.key === key ? 1.7 : 1)
  }
}

/** 构建 6 向解剖方位标记（A 前 / P 后 / L 左 / R 右 / S 上 / I 下） */
function buildOrientationMarks(): THREE.Group {
  const group = new THREE.Group()
  // 世界系：x=L→R（患者右在 x+，放射学前面观时位于屏幕左侧），y=I→S，z=A→P（前为 z-）
  const marks: Array<[string, string, [number, number, number]]> = [
    ['A', '#FFB74D', [0, 0, -0.66]],
    ['P', '#FFB74D', [0, 0, 0.66]],
    ['S', '#4FC3F7', [0, 0.66, 0]],
    ['I', '#4FC3F7', [0, -0.66, 0]],
    ['L', '#A5D6A7', [-0.66, 0, 0]],
    ['R', '#A5D6A7', [0.66, 0, 0]]
  ]
  for (const [text, color, p] of marks) {
    const cnv = document.createElement('canvas')
    cnv.width = 64
    cnv.height = 64
    const c = cnv.getContext('2d')!
    c.font = '700 44px "IBM Plex Mono", monospace'
    c.textAlign = 'center'
    c.textBaseline = 'middle'
    c.fillStyle = color
    c.fillText(text, 32, 34)
    const tex = new THREE.CanvasTexture(cnv)
    tex.colorSpace = THREE.SRGBColorSpace
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, opacity: 0.85, depthTest: false }))
    sp.position.set(...p)
    sp.userData.sidePos = p
    sp.scale.set(0.07, 0.07, 1)
    sp.renderOrder = 10
    group.add(sp)
    orientationSprites.push(sp)
  }
  return group
}

/** 剖切平面切换：更新 shader uniform，并隐藏被切除侧的脑区标注/方位标 */
function applyClip(): void {
  const d = CLIP_DIRS[clipMode.value]
  if (material) {
    material.uniforms.uClipDir.value.set(d[0], d[1], d[2])
    material.uniforms.uClipPos.value = 0
  }
  const cut = (p: THREE.Vector3 | number[]): boolean =>
    Array.isArray(p) ? p[0] * d[0] + p[1] * d[1] + p[2] * d[2] > 0 : p.x * d[0] + p.y * d[1] + p.z * d[2] > 0
  for (const r of annRefs) {
    const gone = cut(r.pos)
    r.dot.visible = showAnnotations.value && !gone
    r.sprite.visible = showAnnotations.value && labelVisible.value.has(r.region.key) && !gone
  }
  for (const sp of orientationSprites) {
    sp.visible = !cut(sp.userData.sidePos as [number, number, number])
  }
}

function toggleAnnotations(): void {
  showAnnotations.value = !showAnnotations.value
  if (annotationGroup) annotationGroup.visible = showAnnotations.value
  applyClip()
}

function initScene(): void {
  const box = canvasBox.value
  if (!box) return
  const w = box.clientWidth || 600
  const h = box.clientHeight || 420

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setSize(w, h)
  renderer.setClearColor(0x0b0f14, 1)
  box.appendChild(renderer.domElement)
  renderer.domElement.addEventListener('click', onCanvasClick)
  renderer.domElement.addEventListener('pointermove', onPointerMove)
  raycaster = new THREE.Raycaster()
  // 默认仅显示 AI 异常脑区标签（正常脑区点击彩点/图例再显示）
  labelVisible.value = new Set(regions.filter((r) => isAbnormal(r.region.name)).map((r) => r.region.key))

  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 10)
  // 从前上右方俯视（额极朝向观察者一侧）；患者 R 在屏幕左侧（放射学定侧）
  camera.position.set(1.1, 0.9, -1.4)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.autoRotate = autoRotate.value
  controls.autoRotateSpeed = 1.2

  material = new THREE.ShaderMaterial({
    vertexShader: VERT,
    fragmentShader: FRAG,
    transparent: true,
    depthWrite: false,
    uniforms: {
      uTexMri: { value: null },
      uTexPet: { value: null },
      uThreshold: { value: threshold.value },
      uMode: { value: 0 },
      uCamPos: { value: new THREE.Vector3() },
      uClipDir: { value: new THREE.Vector3(0, 0, 0) },
      uClipPos: { value: 0 }
    }
  })
  const geo = new THREE.BoxGeometry(1, 1, 1)
  mesh = new THREE.Mesh(geo, material)
  scene.add(mesh)

  // 脑区标注 + 方位标记（始终朝向相机、不参与体绘制深度）
  annotationGroup = buildAnnotations()
  scene.add(annotationGroup)
  scene.add(buildOrientationMarks())
  applyClip()

  const loop = (): void => {
    rafId = requestAnimationFrame(loop)
    if (controls && camera && material) {
      controls.update()
      material.uniforms.uCamPos.value.copy(camera.position)
      material.uniforms.uThreshold.value = threshold.value
    }
    if (renderer && scene && camera) renderer.render(scene, camera)
  }
  loop()

  resizeObs = new ResizeObserver(() => {
    if (!box || !renderer || !camera) return
    const cw = box.clientWidth
    const ch = box.clientHeight
    if (cw === 0 || ch === 0) return
    renderer.setSize(cw, ch)
    camera.aspect = cw / ch
    camera.updateProjectionMatrix()
  })
  resizeObs.observe(box)
}

/** 拉取一张体数据并创建 3D 纹理 */
async function fetchTexture(mod: 'MRI' | 'PET'): Promise<THREE.Data3DTexture> {
  const vol = await loadVolume3D(props.caseId, mod, 64)
  const [nx, ny, nz] = vol.dims
  const tex = new THREE.Data3DTexture(vol.data, nx, ny, nz)
  tex.format = THREE.RedFormat
  tex.type = THREE.UnsignedByteType
  tex.minFilter = THREE.LinearFilter
  tex.magFilter = THREE.LinearFilter
  tex.unpackAlignment = 1
  tex.needsUpdate = true
  return tex
}

/** 判空释放旧 3D 纹理（独立函数隔离控制流窄化，保证替换旧纹理前不遗漏 null 情形） */
function disposeTexture(tex: THREE.Data3DTexture | null): void {
  tex?.dispose()
}

/** reload 请求序号：快速连切模态时，仅最新一次请求允许落盘纹理，过期回包直接丢弃 */
let reloadSeq = 0
/** 组件已卸载标志：卸载后在途回包不得再写入已释放的纹理变量 */
let disposed = false

async function reload(): Promise<void> {
  // 捕获本次请求序号；后续回包若非最新或组件已卸载，则丢弃新建纹理
  const mySeq = ++reloadSeq
  loading.value = true
  errorMsg.value = ''
  try {
    const needMri = modality.value !== 'PET'
    const needPet = modality.value !== 'MRI'
    if (needMri && !textureMri) {
      const texMri = await fetchTexture('MRI')
      // 过期回包或卸载后回包：立即释放新建纹理，避免覆盖旧纹理造成 GPU 泄漏
      if (mySeq !== reloadSeq || disposed) {
        texMri.dispose()
        return
      }
      // 正常替换前先释放被替换的旧纹理（首次加载为 null，判空跳过）
      disposeTexture(textureMri)
      textureMri = texMri
    }
    if (needPet && !texturePet) {
      try {
        const texPet = await fetchTexture('PET')
        if (mySeq !== reloadSeq || disposed) {
          texPet.dispose()
          return
        }
        disposeTexture(texturePet)
        texturePet = texPet
      } catch {
        // PET 缺失时融合/单 PET 降级为 MRI
        if (modality.value !== 'MRI') {
          errorMsg.value = '该病例缺少 PET 影像，已显示 MRI'
          modality.value = 'MRI'
        }
      }
    }
    if (material) {
      material.uniforms.uTexMri.value = textureMri
      material.uniforms.uTexPet.value = texturePet
      material.uniforms.uMode.value = modality.value === 'PET' ? 1 : modality.value === 'FUSION' ? 2 : 0
    }
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '3D 体数据加载失败（仅真实影像病例支持）'
  } finally {
    // 仅最新一次请求可关闭 loading，避免过期回包提前关掉在途新请求的加载态
    if (mySeq === reloadSeq && !disposed) loading.value = false
  }
}

watch(autoRotate, (v) => {
  if (controls) controls.autoRotate = v
})

/**
 * 释放标注组子节点 GPU 资源（脑区标签 Sprite / 锚点球 Mesh）
 * watcher 重建标注与组件卸载共用，避免 CanvasTexture/SphereGeometry/Material 泄漏。
 */
function disposeAnnotationGroup(): void {
  if (!annotationGroup) return
  scene?.remove(annotationGroup)
  annotationGroup.traverse((o) => {
    if (o instanceof THREE.Sprite) {
      o.material.map?.dispose()
      o.material.dispose()
    } else if (o instanceof THREE.Mesh) {
      o.geometry.dispose()
      ;(o.material as THREE.Material).dispose()
    }
  })
}

/** AI 分析完成后异常脑区变化 → 重建标注并刷新默认标签集 */
watch(
  () => props.abnormalRegions,
  () => {
    if (!scene || !annotationGroup) return
    labelVisible.value = new Set(regions.filter((r) => isAbnormal(r.region.name)).map((r) => r.region.key))
    disposeAnnotationGroup()
    annotationGroup = buildAnnotations()
    annotationGroup.visible = showAnnotations.value
    scene.add(annotationGroup)
    applyClip()
  }
)

onMounted(() => {
  initScene()
  void reload()
})

onBeforeUnmount(() => {
  // 标记卸载：在途 fetchTexture 回包后只释放自身纹理，不再写已清理的变量
  disposed = true
  cancelAnimationFrame(rafId)
  renderer?.domElement.removeEventListener('click', onCanvasClick)
  renderer?.domElement.removeEventListener('pointermove', onPointerMove)
  resizeObs?.disconnect()
  // 释放标注组子节点（脑区标签 Sprite / 锚点球 Mesh）的 GPU 资源
  disposeAnnotationGroup()
  // 释放方位标精灵的贴图与材质
  for (const sp of orientationSprites) {
    sp.material.map?.dispose()
    sp.material.dispose()
  }
  orientationSprites = []
  textureMri?.dispose()
  texturePet?.dispose()
  mesh?.geometry.dispose()
  material?.dispose()
  controls?.dispose()
  renderer?.dispose()
  if (renderer?.domElement.parentElement) renderer.domElement.parentElement.removeChild(renderer.domElement)
})
</script>

<style scoped>
.vol-renderer {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 420px;
  background: #0b0f14;
  border-radius: 8px;
  overflow: hidden;
}
.vol-canvas-box {
  position: absolute;
  inset: 0;
}
.vol-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(11, 15, 20, 0.72);
  z-index: 5;
}
.vol-toolbar {
  position: absolute;
  top: 10px;
  left: 10px;
  display: flex;
  align-items: center;
  background: rgba(8, 12, 18, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  padding: 6px 10px;
  z-index: 6;
}
.vol-clipbar {
  position: absolute;
  top: 52px;
  left: 10px;
  display: flex;
  align-items: center;
  background: rgba(8, 12, 18, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  padding: 4px 10px;
  z-index: 6;
}
.vol-legend {
  position: absolute;
  bottom: 10px;
  left: 10px;
  background: rgba(8, 12, 18, 0.78);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  padding: 8px 12px;
  z-index: 6;
  max-height: 60%;
  overflow-y: auto;
}
.vol-fusion-legend {
  position: absolute;
  top: 96px;
  left: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(8, 12, 18, 0.78);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  padding: 5px 10px;
  z-index: 6;
  font-size: 11px;
  color: rgba(226, 232, 240, 0.75);
}
.vol-fusion-legend .dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-left: 6px;
}
.vol-fusion-legend .dot:first-child {
  margin-left: 0;
}
.vol-hint {
  position: absolute;
  bottom: 8px;
  right: 12px;
  font-size: 11px;
  color: rgba(226, 232, 240, 0.55);
  z-index: 6;
}
</style>
