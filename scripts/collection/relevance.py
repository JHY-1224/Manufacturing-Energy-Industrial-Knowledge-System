"""Conservative caption screening for process-manufacturing candidates; images still require visual review."""
import re
PROCESS=re.compile(r'工[艺藝]|流程|生[产產]|制[备備造作]|合成|提[炼煉纯]|聚合|[煅焙]烧|熔[炼煉]|热[处處]理|原理|系[统統]|循[环環]|转换|轉換|process|production|manufactur|synthesis|refining|PFD|P.?I.?D',re.I)
MATERIAL=re.compile(r'PVC|聚氯|聚乙|聚丙|聚合|高分子|塑[料膠胶]|树脂|樹脂|正[极極]|负[极極]|[电電][极極]|石墨|碳[化素材]|冶[炼煉金]|[钢鋼]|金[属屬]|[轧軋]|[烧燒]结|陶瓷|水泥|玻璃|耐火|[铝鋁铜銅]|焊[接材]|材料|化工|化[学學]|weld|electrode|wire|polymer|resin|plastic|graphite|steel|metal|chemical|cathode|anode',re.I)
ENERGY=re.compile(r'能源|[炼煉]油|石油|[炼煉][化製制]|油[气氣]|煤|焦[化炉爐]|[气氣]化|天然[气氣]|[发發][电電]|[电電]站|[火水核][电電]|光[伏热熱]|[风風][电電力]|[锅鍋]炉|汽[轮輪]|[热熱]力|[氢氫]|[电電]解水|甲醇|燃[料气氣]|生物[质質]|hydrogen|electrolys|refiner|petroleum|gasification|fuel|power plant|boiler|turbine|methanol|biomass',re.I)
NOISE=re.compile(r'股票|股[价價市]|景[气氣]指数|展[会览會覽]|招聘|外[劳勞]|校[园園]|[涨漲]幅|[利利润潤]总额|GDP|绘制教程|怎么.*绘|如何.*绘|图标|logo|壁纸|海报|思维导图|stock price|job vacancy|wallpaper',re.I)
ENERGY_EQUIPMENT=re.compile(r'[锂鋰][电電]|[电電]池包|模[组組]|光伏[组組]件|组件[生制]|[锂鋰][离離]子|battery pack|lithium.ion|solar module',re.I)


def reject_reason(domain,category,item):
 if category.get('relevance_required')!='process_manufacturing':return None
 text=item.get('caption','')
 if NOISE.search(text):return 'Caption describes macro/news/marketing/drawing tutorial rather than a production process'
 if not PROCESS.search(text):return 'No production/process/operation evidence in source caption'
 if domain['id']=='materials' and not MATERIAL.search(text):return 'Material-production context is not explicit'
 if domain['id']=='energy':
  if not ENERGY.search(text):return 'Energy-production or conversion context is not explicit'
  if ENERGY_EQUIPMENT.search(text):return 'Energy-equipment assembly is outside the current energy process-manufacturing core'
 return None
