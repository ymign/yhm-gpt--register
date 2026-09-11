<script setup>
import SmsPhoneLedgerPanel from '@/components/SmsPhoneLedgerPanel.vue'
</script>

<template>
  <div class="ledger-page">
    <SmsPhoneLedgerPanel />
  </div>
</template>

<style scoped>
.ledger-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
