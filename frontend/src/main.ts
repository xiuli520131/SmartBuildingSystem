import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import MonitorView from './views/MonitorView.vue'
import PeopleView from './views/PeopleView.vue'
import RecordsView from './views/RecordsView.vue'
import AlertsView from './views/AlertsView.vue'
import './styles.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: MonitorView },
    { path: '/people', component: PeopleView },
    { path: '/records', component: RecordsView },
    { path: '/alerts', component: AlertsView },
  ],
})

createApp(App).use(router).mount('#app')
