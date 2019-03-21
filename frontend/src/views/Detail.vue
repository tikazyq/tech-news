<template>
  <div class="detail">
    <div class="left"></div>
    <div class="center">
      <h1 class="title">{{article.title}}</h1>
      <div class="content" v-html="article.content">
      </div>
    </div>
    <div class="right"></div>
  </div>
</template>

<script>
import axios from 'axios'

export default {
  name: 'Detail',
  data () {
    return {
      article: {}
    }
  },
  computed: {
    id () {
      return this.$route.params.id
    }
  },
  created () {
    axios.get(`http://localhost:5000/results/${this.id}`)
      .then(response => {
        this.article = response.data
      })
  }
}
</script>

<style scoped>
  .detail {
    display: flex;
  }

  .left {
    flex-basis: 20%;
  }

  .right {
    flex-basis: 20%;
  }

  .center {
    flex-basis: 60%;
    text-align: left;
  }

  .title {

  }
</style>
