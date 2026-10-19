; Locally authored regression input; the upstream file was omitted from v2.3.7.
; A branch merges a raw buffer cursor with null. Only the non-null arm reads it.
; The vector read must remain four word accesses plus the trailing scalar read.
define void @nullable_branch_cursor(ptr addrspace(1) readonly %src, ptr addrspace(1) writeonly %out, <3 x i32> %gid) {
entry:
  %x = extractelement <3 x i32> %gid, i32 0
  %choose = icmp ne i32 %x, 0
  br i1 %choose, label %present, label %absent
present:
  %start = getelementptr inbounds i8, ptr addrspace(1) %src, i64 16
  br label %merge
absent:
  br label %merge
merge:
  %cursor = phi ptr addrspace(1) [ %start, %present ], [ null, %absent ]
  %isnull = icmp eq ptr addrspace(1) %cursor, null
  br i1 %isnull, label %zero, label %read
read:
  %values = load <4 x float>, ptr addrspace(1) %cursor, align 16
  %a = extractelement <4 x float> %values, i32 0
  %b = extractelement <4 x float> %values, i32 1
  %c = extractelement <4 x float> %values, i32 2
  %d = extractelement <4 x float> %values, i32 3
  %tail = getelementptr inbounds i8, ptr addrspace(1) %cursor, i64 16
  %e = load float, ptr addrspace(1) %tail, align 4
  %ab = fadd float %a, %b
  %cd = fadd float %c, %d
  %abcd = fadd float %ab, %cd
  %sum = fadd float %abcd, %e
  br label %exit
zero:
  br label %exit
exit:
  %result = phi float [ %sum, %read ], [ 0.000000e+00, %zero ]
  store float %result, ptr addrspace(1) %out, align 4
  ret void
}

!air.kernel = !{!0}
!0 = !{ptr @nullable_branch_cursor, !1, !2}
!1 = !{}
!2 = !{!3, !4, !5}
!3 = !{i32 0, !"air.buffer", !"air.location_index", i32 0, i32 1, !"air.read", !"air.address_space", i32 1, !"air.arg_type_size", i32 1, !"air.arg_type_align_size", i32 1, !"air.arg_type_name", !"uchar", !"air.arg_name", !"src"}
!4 = !{i32 1, !"air.buffer", !"air.location_index", i32 1, i32 1, !"air.write", !"air.address_space", i32 1, !"air.arg_type_size", i32 4, !"air.arg_type_align_size", i32 4, !"air.arg_type_name", !"float", !"air.arg_name", !"out"}
!5 = !{i32 2, !"air.thread_position_in_grid", !"air.arg_type_name", !"uint3", !"air.arg_name", !"gid"}
