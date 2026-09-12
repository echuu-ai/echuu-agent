"""
戏剧性放大器 - 把普通描述变成精彩描述
"""


class DramaAmplifier:
    """
    放大器 - 把普通描述变成精彩描述
    """

    AMPLIFIERS = {
        "specificity": {
            "name": "具体化",
            "rule": "用一个具体物品/数字代表状态",
            "before": "我很穷",
            "after": "一个月给自己定额三瓶可乐",
        },
        "internal_voice": {
            "name": "内心独白",
            "rule": "说出当时脑子里的声音",
            "before": "我想吃",
            "after": "我就站那想，就一颗应该没事吧？",
        },
        "failed_logic": {
            "name": "失败的逻辑",
            "rule": "说出那个事后看很蠢的推理",
            "before": "我试图掩盖",
            "after": "我当时想着重新摆整齐应该就看不出来少了一点，事后想想根本瞒不住",
        },
        "body_memory": {
            "name": "身体记忆",
            "rule": "用身体感受代替情绪词",
            "before": "我很紧张",
            "after": "心都颤了一下",
        },
        "delayed_reveal": {
            "name": "延迟揭示",
            "rule": "关键信息前停顿/绕路",
            "before": "她原谅我了",
            "after": "她敲门的时候我心跳到嗓子眼...然后她说...",
        },
        "self_mockery": {
            "name": "事后自嘲",
            "rule": "现在的我嘲笑当时的我",
            "before": "我当时很蠢",
            "after": "我现在想想怎么就没发现，那包装袋明显扁了啊",
        },
        "universal_hook": {
            "name": "共鸣钩子",
            "rule": "把观众拉进来",
            "before": "那个东西很香",
            "after": "那种味道你们懂吧？闻到就走不动那种",
        },
    }

    def list_amplifiers(self) -> dict:
        """返回所有放大器定义"""
        return self.AMPLIFIERS
