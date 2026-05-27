export default function SettingsTab() {
  return (
    <div>
      <h1 className="text-xl font-bold">⚙️ 设置</h1>
      <p className="mb-4 mt-0.5 text-[13px] text-mut">连接账号 / 关注词 / 排序口味</p>

      <div className="rounded-xl border border-line bg-panel p-4">
        <div className="text-[13px] text-ink/80">
          热度怎么排:看点赞 + 评论 + 转发,越新的越靠前(当前是默认口味,以后可调)
        </div>
      </div>

      <div className="py-9 text-center text-sm text-mut">
        真功能(密钥 / 关注词 / 口味调节)做好后接到这里
      </div>
    </div>
  );
}
