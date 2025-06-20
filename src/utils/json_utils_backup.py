# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: MIT

import logging
import json
import json_repair

logger = logging.getLogger(__name__)


def repair_json_output(content: str) -> str:
    """
    Repair and normalize JSON output.
    
    This function handles various formats including:
    - Regular JSON objects
    - JSON wrapped in code blocks
    - JSON with thinking model output (contains thoughts)

    Args:
        content (str): String content that may contain JSON

    Returns:
        str: Repaired JSON string, or original content if not JSON
    """
    content = content.strip()
    
    # 【修复】处理思考模型的输出格式
    # 思考模型通常在开头包含思考内容，然后是JSON
    if '"locale"' in content and '"title"' in content:
        # 尝试找到JSON开始的位置
        json_start_patterns = [
            '{"locale"',  # 直接以JSON开始
            '\n{"locale"',  # 换行后JSON开始  
            '}\n{"locale"',  # 上一个JSON结束后的新JSON
        ]
        
        json_start = -1
        for pattern in json_start_patterns:
            pos = content.find(pattern)
            if pos != -1:
                if pattern.startswith('\n') or pattern.startswith('}'):
                    json_start = pos + len(pattern) - len('{"locale"')
                else:
                    json_start = pos
                break
        
        if json_start != -1:
            # 从JSON开始位置提取到末尾
            json_content = content[json_start:].strip()
            logger.info(f"检测到思考模型输出，提取JSON部分，原长度: {len(content)}, JSON长度: {len(json_content)}")
            content = json_content
    
    if content.startswith(("{", "[")) or "```json" in content or "```ts" in content:
        try:
            # If content is wrapped in ```json code block, extract the JSON part
            if content.startswith("```json"):
                content = content.removeprefix("```json")

            if content.startswith("```ts"):
                content = content.removeprefix("```ts")

            if content.endswith("```"):
                content = content.removesuffix("```")

            # 【修复】尝试清理可能的多余内容
            content = content.strip()
            
            # 【修复】处理数学公式中的反斜杠转义问题
            # 替换常见的LaTeX数学符号，避免JSON解析错误
            math_replacements = {
                r'\(': r'\\(',      # 数学公式开始
                r'\)': r'\\)',      # 数学公式结束
                r'\[': r'\\[',      # 数学公式开始
                r'\]': r'\\]',      # 数学公式结束
                r'\frac': r'\\frac',  # 分数
                r'\sum': r'\\sum',    # 求和
                r'\int': r'\\int',    # 积分
                r'\sqrt': r'\\sqrt',  # 平方根
                r'\alpha': r'\\alpha',  # 希腊字母
                r'\beta': r'\\beta',
                r'\gamma': r'\\gamma',
                r'\delta': r'\\delta',
                r'\epsilon': r'\\epsilon',
                r'\theta': r'\\theta',
                r'\lambda': r'\\lambda',
                r'\mu': r'\\mu',
                r'\pi': r'\\pi',
                r'\sigma': r'\\sigma',
                r'\omega': r'\\omega',
            }
            
            for original, replacement in math_replacements.items():
                if original in content:
                    content = content.replace(original, replacement)
                    logger.debug(f"替换数学符号: {original} -> {replacement}")
            
            # 如果内容包含多个JSON对象，只取第一个完整的
            if content.count('{') > content.count('}'):
                # JSON可能不完整，尝试找到最后一个完整的}
                brace_count = 0
                last_complete_pos = -1
                for i, char in enumerate(content):
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            last_complete_pos = i + 1
                            break
                
                if last_complete_pos > 0:
                    content = content[:last_complete_pos]
                    logger.info(f"截取完整JSON到位置: {last_complete_pos}")

            # Try to repair and parse JSON
            repaired_content = json_repair.loads(content)
            
            # 【修复】过滤空步骤以避免验证错误
            if isinstance(repaired_content, dict) and "steps" in repaired_content:
                original_steps = repaired_content["steps"]
                if isinstance(original_steps, list):
                    # 过滤掉空字典或无效步骤
                    valid_steps = []
                    for step in original_steps:
                        if isinstance(step, dict) and step and any(step.values()):
                            # 检查是否有必需的字段
                            required_fields = ["need_search", "title", "description", "step_type"]
                            if all(field in step for field in required_fields):
                                valid_steps.append(step)
                            else:
                                logger.warning(f"跳过不完整的步骤: {step}")
                        else:
                            logger.warning(f"跳过空步骤: {step}")
                    
                    if len(valid_steps) != len(original_steps):
                        logger.info(f"过滤步骤: 原始{len(original_steps)}个，有效{len(valid_steps)}个")
                        repaired_content["steps"] = valid_steps
            
            return json.dumps(repaired_content, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"JSON repair failed: {e}")
            logger.debug(f"Failed content preview: {content[:200]}...")
    return content
