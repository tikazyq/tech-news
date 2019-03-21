import Vue from 'vue'
import Router from 'vue-router'
import List from './views/List'
import Detail from './views/Detail'

Vue.use(Router)

export default new Router({
  mode: 'hash',
  base: process.env.BASE_URL,
  routes: [
    {
      path: '/',
      name: 'List',
      component: List
    },
    {
      path: '/:id',
      name: 'Detail',
      component: Detail
    }
  ]
})
