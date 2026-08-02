import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as Icons from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import './styles/main.css'
import './styles/overview.css'
import './styles/ops-console.css'
import './styles/layout-v2.css'
import './styles/data-pages.css'
import './styles/live-data.css'
import './styles/alert-detail.css'

const app = createApp(App)
Object.entries(Icons).forEach(([name, component]) => app.component(name, component))
app.use(createPinia()).use(router).use(ElementPlus).mount('#app')
