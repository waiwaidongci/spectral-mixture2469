<script setup lang="ts">import {useAuth} from './stores/auth';import Login from './pages/Login.vue';const auth=useAuth();</script><template><RouterView v-if="auth.user"/><Login v-else/></template>
