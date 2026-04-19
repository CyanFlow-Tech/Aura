import torch

file_path = 'pretrained_models/CosyVoice-300M-SFT/spk2info.pt'

try:
    speaker_dict = torch.load(file_path, map_location='cpu')
    
    speaker_ids = list(speaker_dict.keys())
    
    print(f"--- 查询成功 ---")
    print(f"总共包含音色数量: {len(speaker_ids)}")
    print(f"具体的 Speaker ID 列表如下:")
    for spk_id in speaker_ids:
        print(f"- {spk_id}")
    with open('speaker_ids.txt', 'w') as f:
        for spk_id in speaker_ids:
            f.write(f"{spk_id}\n")
except Exception as e:
    print(f"读取文件失败: {e}")