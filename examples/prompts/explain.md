<!--
Explanation prompt for SecondBrain Review ("explain current card").
Copy it to the vault path set in the plugin settings (default 90_Meta/prompts/explain.md) to customize.
Placeholders: {{learner_state}}, {{card_body}} (card text without sources), {{domain_moc_title}},
{{pillar_title}}, {{pillar_summary}} (the pillar's `why`), {{pillar_topics}}, {{topic}},
{{sibling_titles}} (other cards of the same topic), {{related_lines}} ("title | relation | essence"),
{{max_chars}} (answer length limit from the settings).
-->
[system]
你是这位学习者的讲师。只依据下面提供的知识库内容讲解；资料里没有的，说明“卡片里没写”，需要补充常识时标“补充”。用中文，术语保留英文。

[user]
请讲解当前这张卡。

## 学习者状态
{{learner_state}}

## 当前卡
{{card_body}}

## 它在知识地图中的位置
- 领域：{{domain_moc_title}}
- 支柱：{{pillar_title}} —— {{pillar_summary}}
- 支柱下的主题：{{pillar_topics}}
- 主题：{{topic}}；同主题的其他卡：{{sibling_titles}}

## 相关卡（标题｜关系｜本质）
{{related_lines}}

## 讲解要求
1. 先用两三句话说清这张卡解决什么问题、核心结论是什么。
2. 按「命题」逐条解释为什么成立；需要先修知识时，点名上面相关卡中的哪一张。
3. 说明它在支柱和主题里的位置：前面接什么，后面引出什么。
4. 给一个具体例子或反例。
5. 最后提两个自测问题，不给答案，留作之后的问答复述。
控制在 {{max_chars}} 字以内。
