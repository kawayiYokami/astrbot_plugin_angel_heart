import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

// Exercise the SFC's actual handlers without a browser or an extra test framework.
function loadHandlers(file, names, context) {
  const source = readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8')
    .split('<script setup lang="ts">')[1].split('</script>')[0]
  const tree = ts.createSourceFile(file + '.ts', source, ts.ScriptTarget.Latest, true)
  const handlers = tree.statements.filter(
    (node) => ts.isFunctionDeclaration(node) && names.includes(node.name?.text),
  )
  assert.equal(handlers.length, names.length)
  const { outputText } = ts.transpileModule(handlers.map((node) => node.getText(tree)).join('\n'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None },
  })
  vm.runInNewContext(outputText, context)
  return context
}

const clone = (value) => JSON.parse(JSON.stringify(value))
const globalConfig = () => ({
  wake_interaction: {
    ignored_sender_ids: ['999'],
    alias_wake_cooldown_seconds: 30,
    enter_on_mention_only: true,
  },
})

function editor(config = {}, globals = globalConfig()) {
  let stored = clone(config)
  let savedGlobal = clone(globals)
  const posts = []
  const tpl = { id: 'old-template', description: '', config: clone(config) }
  const panel = loadHandlers('ChatConfigPanel.vue', [
    'selectTemplate', 'saveTemplate', 'loadAll', 'savePluginConfig',
  ], {
    profilesLoadVersion: 0,
    templates: { value: [tpl] },
    selectedId: { value: null },
    viewMode: { value: 'config' },
    configModel: { value: {} },
    templateDefaults: { value: clone(globals) },
    templateBaseline: { value: '' },
    settingsValues: { value: clone(globals) },
    settingsBaseline: { value: JSON.stringify(globals) },
    chats: { value: [] },
    currentTemplate: { get value() { return tpl } },
    saving: { value: false },
    savingSettings: { value: false },
    message: { success() {}, error(message) { throw new Error(message) } },
    async apiPost(route, body) {
      posts.push({ route, body: clone(body) })
      if (route === 'profiles/update') stored = clone(body.config)
      if (route === 'plugin_config/save') savedGlobal = clone(body.values)
    },
    async apiGet(route) {
      if (route === 'chats') return []
      assert.equal(route, 'profiles')
      return { templates: [{ ...tpl, config: clone(stored) }], bindings: {}, global_config: clone(savedGlobal) }
    },
  })
  panel.selectTemplate(tpl.id)
  const form = loadHandlers('SchemaForm.vue', ['getIn', 'setIn'], {
    props: {
      get modelValue() { return panel.configModel.value },
      get fallbackValues() { return panel.templateDefaults.value },
    },
    emit(event, model) {
      assert.equal(event, 'update:modelValue')
      panel.configModel.value = model
    },
  })
  return { panel, form, posts, tpl, globals }
}

const wakePath = (key) => ['wake_interaction', key]

test('saving an unrelated edit keeps old template wake fields inherited', async () => {
  const { panel, form, posts, tpl } = editor({ wake_interaction: { alias: 'AngelHeart' } })
  assert.deepEqual(clone(form.getIn(wakePath('ignored_sender_ids'))), ['999'])
  assert.equal(form.getIn(wakePath('alias_wake_cooldown_seconds')), 30)
  form.setIn(wakePath('alias'), 'NewName')
  await panel.saveTemplate()
  assert.deepEqual(posts[0].body.config, { wake_interaction: { alias: 'NewName' } })
  assert.equal(tpl.config.wake_interaction.alias, 'AngelHeart')
  assert.equal(panel.templateBaseline.value, JSON.stringify(panel.configModel.value))
})

test('explicit empty, zero and false overrides survive unrelated edits', async () => {
  const overrides = { ignored_sender_ids: [], alias_wake_cooldown_seconds: 0, enter_on_mention_only: false }
  const { panel, form, posts } = editor({ wake_interaction: overrides })
  for (const [key, value] of Object.entries(overrides)) {
    assert.deepEqual(clone(form.getIn(wakePath(key))), value)
  }
  form.setIn(wakePath('alias'), 'NewName')
  await panel.saveTemplate()
  assert.deepEqual(posts[0].body.config.wake_interaction, { ...overrides, alias: 'NewName' })
})

test('editing inherited values creates only the requested overrides in an empty template', async () => {
  const { panel, form, posts, globals } = editor()
  assert.equal(form.getIn(wakePath('alias_wake_cooldown_seconds')), 30)
  form.setIn(wakePath('alias_wake_cooldown_seconds'), 0)
  await panel.saveTemplate()
  assert.deepEqual(posts[0].body.config, { wake_interaction: { alias_wake_cooldown_seconds: 0 } })
  form.setIn(wakePath('ignored_sender_ids'), [])
  form.setIn(wakePath('enter_on_mention_only'), false)
  await panel.saveTemplate()
  assert.deepEqual(posts[1].body.config, { wake_interaction: {
    alias_wake_cooldown_seconds: 0, ignored_sender_ids: [], enter_on_mention_only: false,
  } })
  assert.deepEqual(globals, globalConfig())
  assert.deepEqual(clone(panel.templateDefaults.value), globalConfig())
})

test('a saved global change refreshes inherited values without freezing them into a template', async () => {
  const { panel, form, posts } = editor({ wake_interaction: { alias: 'AngelHeart' } })
  panel.settingsValues.value.wake_interaction.alias_wake_cooldown_seconds = 60
  assert.equal(form.getIn(wakePath('alias_wake_cooldown_seconds')), 30)
  await panel.savePluginConfig()
  assert.equal(form.getIn(wakePath('alias_wake_cooldown_seconds')), 60)
  form.setIn(wakePath('alias'), 'NewName')
  await panel.saveTemplate()
  assert.deepEqual(posts.at(-1).body.config, { wake_interaction: { alias: 'NewName' } })
})

test('the global form retains its existing behavior without fallback values', () => {
  const context = {
    props: { modelValue: { wake_interaction: { alias_wake_cooldown_seconds: 0 } } },
    emit(_event, model) { context.props.modelValue = model },
  }
  const form = loadHandlers('SchemaForm.vue', ['getIn', 'setIn'], context)
  assert.equal(form.getIn(wakePath('alias_wake_cooldown_seconds')), 0)
  assert.equal(form.getIn(wakePath('ignored_sender_ids')), undefined)
  form.setIn(wakePath('ignored_sender_ids'), ['999'])
  assert.deepEqual(clone(form.props.modelValue.wake_interaction.ignored_sender_ids), ['999'])
})


test('a delayed initial response cannot overwrite the refreshed global values', { timeout: 1000 }, async () => {
  const { panel, form } = editor({ wake_interaction: { alias: 'AngelHeart' } })
  const pending = []
  let requestsReady
  const bothRequested = new Promise((resolve) => { requestsReady = resolve })
  panel.apiGet = (route) => route === 'chats' ? Promise.resolve([])
    : new Promise((resolve) => {
      pending.push(resolve)
      if (pending.length === 2) requestsReady()
    })
  const firstLoad = panel.loadAll()
  panel.settingsValues.value.wake_interaction.alias_wake_cooldown_seconds = 60
  const saving = panel.savePluginConfig()
  await bothRequested
  assert.equal(pending.length, 2)
  const response = (seconds) => ({
    templates: clone(panel.templates.value), bindings: {},
    global_config: { wake_interaction: { alias_wake_cooldown_seconds: seconds } },
  })
  pending[1](response(60))
  await saving
  assert.equal(form.getIn(wakePath('alias_wake_cooldown_seconds')), 60)
  pending[0](response(30))
  await firstLoad
  assert.equal(form.getIn(wakePath('alias_wake_cooldown_seconds')), 60)
})
