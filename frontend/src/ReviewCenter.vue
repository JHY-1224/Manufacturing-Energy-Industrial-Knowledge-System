<script setup>
import {ref,watch,onMounted,onBeforeUnmount} from 'vue';
import {request} from './workbench-api';
import StructureEvidence from './StructureEvidence.vue';
import VersionDiff from './VersionDiff.vue';
const props=defineProps({bases:{type:Array,default:()=>[]},initialDocumentId:{type:String,default:''}});
const emit=defineEmits(['view-chunks','compare-evidence']);
const labels={unreviewed:'未审核',draft:'审核草稿',published:'已审核发布',withdrawn:'已撤回',needs_review:'修订后待复核'};
const scope=ref(''),state=ref(''),query=ref(''),page=ref(1),total=ref(0),documents=ref([]),counts=ref({});
const selectedId=ref(props.initialDocumentId),selected=ref(null),chunks=ref([]),chunkPage=ref(1),chunkTotal=ref(0);
const quality=ref(null);
const history=ref([]),historyPage=ref(1),historyTotal=ref(0),reviewer=ref(''),note=ref(''),applicability=ref(''),busy=ref(false),error=ref(''),notice=ref('');
let sequence=0,detailSequence=0,timer;
async function load(){const seq=++sequence;error.value='';try{
 const params=new URLSearchParams({page:String(page.value),page_size:'50',q:query.value});
 if(scope.value)params.set('knowledge_base_id',scope.value);if(state.value)params.set('review_state',state.value);
 const [result,summary]=await Promise.all([request('/documents?'+params),request('/reviews/summary')]);
 if(seq!==sequence)return;documents.value=result.items;total.value=result.total;counts.value=summary.counts;
}catch(e){if(seq===sequence)error.value=e.message;}}
async function select(id,reset=true){const seq=++detailSequence;selectedId.value=id;if(reset){chunkPage.value=1;historyPage.value=1;selected.value=null;chunks.value=[];history.value=[];}error.value='';
 try{const [review,evidence,events,profile]=await Promise.all([request(`/documents/${id}/review`),request(`/chunks?document_id=${id}&page=${chunkPage.value}&page_size=10`),request(`/documents/${id}/review/history?page=${historyPage.value}&page_size=20`),request(`/documents/${id}/quality-profile`)]);
 if(seq!==detailSequence)return;selected.value=review;quality.value=profile;chunks.value=evidence.items;chunkTotal.value=evidence.total;history.value=events.items;historyTotal.value=events.total;
 if(reset){note.value='';applicability.value=review.state==='published'?review.record.applicability:'';}
 }catch(e){if(seq===detailSequence){selected.value=null;error.value=e.message;}}
}
async function transition(target){if(busy.value||!selected.value)return;
 if(target==='published'&&!confirm('确认发布当前修订的审核结果？请先核对原件、数值单位、适用条件，并填写审核说明。'))return;
 if(target==='withdrawn'&&!confirm('确认撤回？该资料将退出新的检索和问答，原件与历史记录保留。'))return;
 busy.value=true;error.value='';notice.value='';try{
 await request(`/documents/${selectedId.value}/review`,{method:'POST',body:{state:target,expected_write_version:selected.value.write_version,expected_review_version:selected.value.review_version,reviewer:reviewer.value,note:note.value,applicability:applicability.value}});
 notice.value='审核记录已保存：'+labels[target];await select(selectedId.value);await load();
 }catch(e){error.value=e.message;}finally{busy.value=false;}
}
watch([scope,state],()=>{page.value=1;load();});watch(page,load);
watch(query,()=>{page.value=1;clearTimeout(timer);timer=setTimeout(load,250);});
watch(()=>props.initialDocumentId,id=>{if(id)select(id);});
onMounted(()=>{load();if(selectedId.value)select(selectedId.value);});
onBeforeUnmount(()=>{sequence++;detailSequence++;clearTimeout(timer);});
</script>

<template><div class="wb-review-center">
 <section class="wb-card"><small>KNOWLEDGE REVIEW</small><div class="wb-between"><h2>资料审核与发布</h2><button class="wb-button" :disabled="busy" @click="load();selectedId&&select(selectedId)">刷新审核记录</button></div><p class="wb-muted">核对来源、正文、表格单位和适用条件后，保存审核草稿，再明确发布。资料内容修改后需要重新复核；撤回会停止新的检索引用。</p>
 <div class="wb-chips"><span v-for="(name,key) in labels" :key="key">{{name}} {{counts[key]||0}}</span></div>
 <p class="wb-note">这里记录使用者对当前修订的审核决定。原有来源介绍、机器 OCR 和许可状态分别保留；发布不代表第三方认证或现场质量放行。</p>
 <div v-if="error" class="wb-alert error" role="alert">{{error}}</div><div v-if="notice" class="wb-alert" role="status">{{notice}}</div></section>
 <div class="wb-review-grid"><section class="wb-card"><h3>待核对资料</h3><div class="wb-review-filters"><select v-model="scope" aria-label="审核知识库"><option value="">全部知识库</option><option v-for="b in bases" :key="b.id" :value="b.id">{{b.name}}</option></select><select v-model="state" aria-label="审核状态筛选"><option value="">全部审核状态</option><option v-for="(name,key) in labels" :key="key" :value="key">{{name}}</option></select><input v-model="query" aria-label="搜索审核资料" placeholder="搜索文件名或来源介绍"/></div>
 <div class="wb-task-list"><button v-for="doc in documents" :key="doc.document_id" :class="{active:selectedId===doc.document_id}" :disabled="busy" @click="select(doc.document_id)"><strong>{{doc.source_name||doc.title}}</strong><p>{{labels[doc.knowledge_review_state]||'未审核'}} · {{doc.chunk_count}} chunks</p></button><p v-if="!documents.length" class="wb-empty">没有符合条件的资料。</p></div>
 <div class="wb-between wb-pagination"><button class="wb-button tiny" :disabled="page===1" @click="page--">资料上一页</button><span>{{page}} / {{Math.max(1,Math.ceil(total/50))}} · {{total}} 份</span><button class="wb-button tiny" :disabled="page*50>=total" @click="page++">资料下一页</button></div></section>
 <section class="wb-card"><template v-if="selected"><div class="wb-between"><h3>{{selected.source_name}}</h3><span class="wb-badge">{{labels[selected.state]}}</span></div><p class="wb-muted">{{selected.current_revision_id||'尚未解析'}} · 审核版本 {{selected.review_version}}</p><p>来源标记：{{selected.source_review_status}}<br>来源版本：{{selected.source_version||'未声明'}}</p><div class="wb-actions"><a class="wb-button tiny" :href="`/api/v2/workbench/documents/${selectedId}/source${selected.current_revision_id?'?revision_id='+encodeURIComponent(selected.current_revision_id):''}`">下载来源原件</a><button class="wb-button tiny" @click="emit('view-chunks',{document_id:selectedId})">打开原图与 Chunk</button></div>
 <details v-if="quality"><summary>细分类、适用范围与 AI 证据审核（{{quality.reviews.items.length}} 条）</summary><p>{{quality.profile.industry}} · {{quality.profile.evidence_type}} · 来源细分类 {{quality.profile.source_subcategory||'未声明'}} · 版本 {{quality.profile.version||'未声明'}}</p><p>材料 / 设备 / 工况：未逐项核定；适用范围以具体审核记录为准。</p><article v-for="r in quality.reviews.items" :key="r.review_id"><strong>{{r.reviewer}} · {{r.decision}}</strong><p>{{r.scope}}</p><p>{{r.note}}</p></article></details>
 <details v-for="chunk in chunks" :key="chunk.chunk_id" class="wb-raw"><summary>{{chunk.metadata.section||chunk.chunk_id}} · Page {{chunk.metadata.page??'—'}} · {{chunk.enabled?'启用':'停用'}}</summary><pre>{{chunk.content}}</pre></details>
 <div class="wb-between wb-pagination"><button class="wb-button tiny" :disabled="chunkPage===1" @click="chunkPage--;select(selectedId,false)">证据上一页</button><span>{{chunkPage}} / {{Math.max(1,Math.ceil(chunkTotal/10))}}</span><button class="wb-button tiny" :disabled="chunkPage*10>=chunkTotal" @click="chunkPage++;select(selectedId,false)">证据下一页</button></div>
 <form @submit.prevent="transition('draft')"><label class="wb-field">审核人<input v-model="reviewer" aria-label="审核人" maxlength="100" required/></label><label class="wb-field">本次核对说明<textarea v-model="note" aria-label="审核说明" maxlength="3000" required rows="3" placeholder="已核对的来源、数值单位、未知项或撤回原因"/></label><label class="wb-field">适用范围与限制<textarea v-model="applicability" aria-label="适用范围" maxlength="2000" rows="3" placeholder="材料、设备、版本、工况及不可直接用于生产的限制；发布时必填"/></label><div class="wb-actions"><button class="wb-button" :disabled="busy">{{selected.state==='withdrawn'?'恢复为审核草稿':'保存为审核草稿'}}</button><button v-if="selected.state==='draft'" type="button" class="wb-button primary" :disabled="busy||!applicability.trim()||!reviewer.trim()||!note.trim()" @click="transition('published')">发布当前修订</button><button v-if="selected.state!=='withdrawn'" type="button" class="wb-button danger" :disabled="busy||!reviewer.trim()||!note.trim()" @click="transition('withdrawn')">撤回资料</button></div></form>
 <button v-if="quality?.profile.evidence_type==='image_reference'" class="wb-button" @click="emit('compare-evidence',{document_id:selectedId})">打开图文区域对照与画面修正</button>
 <StructureEvidence :document-id="selectedId"/>
 <VersionDiff :document-id="selectedId"/>
 <h4>审核历史</h4><article v-for="(event,index) in history" :key="event.review_version||index" class="wb-recent"><div><strong>{{labels[event.state]}} · {{event.reviewer}}</strong><p>{{event.note}}</p><p>{{event.applicability}}</p><small>{{event.revision_id}} · {{new Date(event.created_at).toLocaleString('zh-CN')}}</small></div></article><p v-if="!history.length" class="wb-muted">尚无审核记录。</p><div v-if="historyTotal>20" class="wb-between wb-pagination"><button class="wb-button tiny" :disabled="historyPage===1" @click="historyPage--;select(selectedId,false)">历史上一页</button><span>{{historyPage}} / {{Math.ceil(historyTotal/20)}}</span><button class="wb-button tiny" :disabled="historyPage*20>=historyTotal" @click="historyPage++;select(selectedId,false)">历史下一页</button></div>
 </template><p v-else class="wb-empty">选择一份资料，查看证据并记录审核决定。</p></section></div>
</div></template>
