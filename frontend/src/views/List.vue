<template>
  <div class="list">
    <div class="left"></div>
    <div class="center">
      <ul class="article-list">
        <li v-for="article in list" :key="article._id" class="article-item">
          <a href="javascript:" @click="showArticle(article._id)" class="title">
            {{article.title}}
          </a>
          <span class="time">
            {{article.ts}}
          </span>
        </li>
      </ul>
    </div>
    <div class="right"></div>
  </div>
</template>

<script>
import axios from 'axios'

export default {
  name: 'List',
  data () {
    return {
      list: []
    }
  },
  methods: {
    showArticle (id) {
      this.$router.push(`/${id}`)
    }
  },
  created () {
    axios.get('http://localhost:5000/results')
      .then(response => {
        this.list = response.data
      })
  }
}
</script>

<style scoped>
  .list {
    display: flex;
  }

  .left {
    flex-basis: 20%;
  }

  .right {
    flex-basis: 20%;
  }

  .article-list {
    text-align: left;
    list-style: none;
  }

  .article-item {
    background: #c3edfb;
    border-radius: 5px;
    padding: 5px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }

  .title {
    flex-basis: auto;
    color: #58769d;
  }

  .time {
    font-size: 10px;
    text-align: right;
    flex-basis: 180px;
  }
</style>
