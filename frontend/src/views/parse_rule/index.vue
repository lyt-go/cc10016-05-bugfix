<template>
  <section class="page" data-module="parse_rule">
    <header class="page-head">
      <div>
        <h2>解析规则管理</h2>
        <p class="page-desc">维护HTML/JSON内容解析规则的选择器校验、版本发布与事件记录，校验结果与发布状态同时提交，被拦截规则不产生字段模板。</p>
      </div>
      <div class="page-actions">
        <span class="mode-switch" data-testid="publish-mode-switch">
          发布模式：
          <label><input type="radio" value="replace" v-model="publishMode" /> 替换（撤下旧模板）</label>
          <label><input type="radio" value="parallel" v-model="publishMode" /> 并行（新旧并存）</label>
        </span>
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记解析规则</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出解析规则清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>选择器校验</th>
          <th>事件状态</th>
          <th>版本角色</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span
              class="badge"
              :class="row._selector_check?.ok ? 'evt-校验通过' : 'evt-校验拦截'"
              :data-testid="`check-${row.id}`"
            >
              {{ row._selector_check?.ok ? '通过' : '拦截' }}
            </span>
            <div v-if="row._selector_check && !row._selector_check.ok" class="event-reason">
              {{ row._selector_check.msg }}
            </div>
          </td>
          <td>
            <span v-if="row._event_status" class="badge" :class="`evt-${row._event_status}`">{{ row._event_status }}</span>
            <span v-else>—</span>
          </td>
          <td>
            <span v-if="row._version_role" class="badge" :class="`role-${row._version_role}`">{{ row._version_role }}</span>
            <span v-else>—</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" :data-testid="`events-${row.id}`" @click="openDrawer(row)">事件记录</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 4" class="empty-state">暂无解析规则数据，可先登记解析规则</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记解析规则</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <p class="page-desc">口径：选择器类型支持 xpath / css / jsonpath / regex；规则版本需为 vX.Y 或 vX.Y.Z，越界值不允许保存。</p>
        <div class="page-actions">
          <button class="btn" type="button" data-testid="cancel-create" @click="closeCreate">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 解析规则事件记录抽屉：每次打开重新拉取，关闭即清空，避免残留旧值 -->
    <template v-if="drawer.visible">
      <div class="drawer-backdrop" data-testid="event-drawer" @click="closeDrawer"></div>
      <aside class="drawer" role="dialog" aria-label="解析规则事件记录">
        <div class="drawer-head">
          <h3>事件记录 · {{ drawer.ruleCode }}</h3>
          <button class="drawer-close" type="button" data-testid="close-drawer" @click="closeDrawer">×</button>
        </div>
        <div class="drawer-body">
          <dl class="detail-grid" v-if="drawer.detail">
            <template v-for="field in columns" :key="field">
              <dt>{{ field }}</dt>
              <dd>{{ drawer.detail[field] ?? '—' }}</dd>
            </template>
            <dt>选择器校验</dt>
            <dd>
              <span
                class="badge"
                :class="drawer.detail._selector_check?.ok ? 'evt-校验通过' : 'evt-校验拦截'"
              >
                {{ drawer.detail._selector_check?.ok ? '通过' : '拦截' }}：{{ drawer.detail._selector_check?.msg }}
              </span>
            </dd>
          </dl>
          <h4>状态轨迹（状态机按序流转）</h4>
          <ul class="event-timeline" data-testid="event-timeline">
            <li v-for="(event, idx) in drawer.events" :key="String(event.id)">
              <span class="badge" :class="`evt-${event.事件状态}`">{{ event.事件状态 }}</span>
              <span v-if="event.版本角色" class="badge" :class="`role-${event.版本角色}`" style="margin-left:6px">{{ event.版本角色 }}</span>
              <div class="event-meta">{{ event.事件类型 }} · {{ event.发生时间 }} · 批次 {{ event.事件批次 }}</div>
              <div class="event-meta">
                选择器 {{ event.选择器类型 }} {{ event.选择器表达式 }} ·
                校验{{ event.校验通过 ? '通过' : '不通过' }} · 同步：{{ event.跨模块同步状态 }}
              </div>
              <div v-if="event.拦截原因" class="event-reason">拦截原因：{{ event.拦截原因 }}</div>
              <ol v-if="event.状态轨迹?.length" class="event-meta">
                <li v-for="(step, sIdx) in event.状态轨迹" :key="sIdx">
                  {{ step.from ?? '—' }} → {{ step.to }}（{{ step.原因 }}）
                </li>
              </ol>
              <div v-if="idx === drawer.events.length - 1 && event.关联模板ID" class="event-meta">
                关联字段模板ID：{{ event.关联模板ID }}
              </div>
            </li>
          </ul>
        </div>
      </aside>
    </template>

    <footer class="page-foot">
      <span>共 {{ total }} 条解析规则记录</span>
      <span v-if="errorMessage" class="error-text" data-testid="page-error">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
type EventRow = Record<string, any>

const ENDPOINT = '/api/parse_rule'
const columns = ["规则编号", "目标字段名", "选择器类型", "选择器表达式", "提取方式", "默认值", "正则过滤", "规则版本"]
const actions = ["提交校验", "标记生效", "标记废弃"]
const statuses = ["草稿", "待校验", "已生效", "已废弃"]
const metricNames = ["解析规则总数", "待校验规则", "已生效规则"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
// 发布模式：replace 撤下旧模板，parallel 新旧模板并存
const publishMode = ref<'replace' | 'parallel'>('replace')
// 抽屉状态独立管理，关闭时整体重置，避免侧栏/抽屉残留旧值
const drawer = reactive<{ visible: boolean; ruleCode: string; events: EventRow[]; detail: Row | null }>({
  visible: false,
  ruleCode: '',
  events: [],
  detail: null,
})

const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[2]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'parse_rule.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function emptyCreateValues() {
  return Object.fromEntries(columns.map((field) => [field, '']))
}

function openCreate() {
  createValues.value = emptyCreateValues()
  errorMessage.value = ''
  showCreate.value = true
}

function closeCreate() {
  // 关闭即清空表单，防止下次打开残留上一次输入
  showCreate.value = false
  createValues.value = emptyCreateValues()
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    closeCreate()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const body: Record<string, unknown> = { action }
    if (action === '标记生效') body.mode = publishMode.value
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      // 选择器被拦截等后端拒绝原因直接展示，规则状态保持不变
      throw new Error(payload.message || '解析规则动作未生效')
    }
    await reload()
    if (drawer.visible && String(drawer.detail?.id) === String(row.id)) {
      await loadDrawer(Number(row.id))
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '解析规则操作失败'
  }
}

async function openDrawer(row: Row) {
  errorMessage.value = ''
  drawer.visible = true
  drawer.ruleCode = String(row['规则编号'] ?? row.id)
  drawer.events = []
  drawer.detail = null
  await loadDrawer(Number(row.id))
}

async function loadDrawer(entryId: number) {
  try {
    const [detailResp, eventResp] = await Promise.all([
      request(`${ENDPOINT}/${entryId}`),
      request(`${ENDPOINT}/events?rule_id=${entryId}&size=100`),
    ])
    drawer.detail = await detailResp.json()
    const eventPayload = await eventResp.json()
    // 事件按时间正序展示，状态机轨迹逐条可见
    drawer.events = (eventPayload.items ?? []).slice().reverse()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '事件记录读取失败'
  }
}

function closeDrawer() {
  // 整体重置抽屉，杜绝残留并显示成旧值
  drawer.visible = false
  drawer.ruleCode = ''
  drawer.events = []
  drawer.detail = null
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('解析规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '解析规则列表读取失败'
  }
}

onMounted(reload)
</script>
