<script setup>
import {ref,watch,onBeforeUnmount} from 'vue';
import {request} from './workbench-api';
const props=defineProps({modelValue:{type:String,default:''},knowledgeBaseId:{type:String,default:''},label:{type:String,default:'筛选文档'}});
const emit=defineEmits(['update:modelValue']);
const query=ref(''),page=ref(1),items=ref([]),total=ref(0),selected=ref(null),error=ref(''),loading=ref(false);
let sequence=0,timer;
async function load(){const seq=++sequence;loading.value=true;error.value='';try{
 const params=new URLSearchParams({page:String(page.value),page_size:'50',compact:'true',q:query.value});
 if(props.knowledgeBaseId)params.set('knowledge_base_id',props.knowledgeBaseId);
 const result=await request('/documents?'+params);
 const picked=props.modelValue&&!result.items.some(d=>d.document_id===props.modelValue)?await request('/documents/'+encodeURIComponent(props.modelValue)):null;
 if(seq!==sequence)return;items.value=result.items;total.value=result.total;selected.value=picked;
}catch(e){if(seq===sequence){items.value=[];total.value=0;error.value=e.message;}}finally{if(seq===sequence)loading.value=false;}}
watch(query,()=>{page.value=1;clearTimeout(timer);timer=setTimeout(load,250);});
watch(()=>props.knowledgeBaseId,()=>{page.value=1;load();});
watch(()=>props.modelValue,()=>{if(props.modelValue&&!items.value.some(d=>d.document_id===props.modelValue))load();});
watch(page,load);load();
onBeforeUnmount(()=>{sequence++;clearTimeout(timer);});
</script>
<template><div class="wb-document-picker"><input v-model="query" :aria-label="label+'名称搜索'" placeholder="搜索文件名"/><select :aria-label="label" :value="modelValue" @change="emit('update:modelValue',$event.target.value)"><option value="">全部文档</option><option v-if="selected&&!items.some(d=>d.document_id===selected.document_id)" :value="selected.document_id">{{selected.source_name||selected.title}}</option><option v-for="doc in items" :key="doc.document_id" :value="doc.document_id">{{doc.source_name||doc.title}}</option></select><div class="wb-picker-pages"><button type="button" :disabled="page===1||loading" aria-label="文档选项上一页" @click="page--">上一页</button><span>{{page}} / {{Math.max(1,Math.ceil(total/50))}} · {{total}} 份</span><button type="button" :disabled="page*50>=total||loading" aria-label="文档选项下一页" @click="page++">下一页</button></div><small v-if="error" role="alert" class="wb-error-text">{{error}}</small></div></template>
