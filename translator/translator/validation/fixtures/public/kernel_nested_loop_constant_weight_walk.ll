; Locally authored regression input; the upstream file was omitted from v2.3.7.
; Nested loops carry a constant-buffer byte cursor through both loop headers.
; Each inner iteration reads a float4 and advances the cursor by sixteen bytes.
define void @nested_weight_walk(ptr addrspace(2) readonly %weights, ptr addrspace(1) writeonly %out) {
entry:
  br label %outer
outer:
  %row = phi i32 [ 0, %entry ], [ %nextrow, %latch ]
  %rowcursor = phi ptr addrspace(2) [ %weights, %entry ], [ %nextcursor, %latch ]
  %rowsum = phi float [ 0.000000e+00, %entry ], [ %sum, %latch ]
  br label %inner
inner:
  %column = phi i32 [ 0, %outer ], [ %nextcolumn, %inner ]
  %cursor = phi ptr addrspace(2) [ %rowcursor, %outer ], [ %nextcursor, %inner ]
  %acc = phi float [ %rowsum, %outer ], [ %sum, %inner ]
  %values = load <4 x float>, ptr addrspace(2) %cursor, align 16
  %a = extractelement <4 x float> %values, i32 0
  %b = extractelement <4 x float> %values, i32 1
  %c = extractelement <4 x float> %values, i32 2
  %d = extractelement <4 x float> %values, i32 3
  %ab = fadd float %a, %b
  %cd = fadd float %c, %d
  %subtotal = fadd float %ab, %cd
  %sum = fadd float %acc, %subtotal
  %nextcursor = getelementptr inbounds <4 x float>, ptr addrspace(2) %cursor, i64 1
  %nextcolumn = add i32 %column, 1
  %morecolumns = icmp ult i32 %nextcolumn, 3
  br i1 %morecolumns, label %inner, label %latch
latch:
  %nextrow = add i32 %row, 1
  %morerows = icmp ult i32 %nextrow, 2
  br i1 %morerows, label %outer, label %exit
exit:
  store float %sum, ptr addrspace(1) %out, align 4
  ret void
}

!air.kernel = !{!0}
!0 = !{ptr @nested_weight_walk, !1, !2}
!1 = !{}
!2 = !{!3, !4}
!3 = !{i32 0, !"air.buffer", !"air.location_index", i32 0, i32 1, !"air.read", !"air.address_space", i32 2, !"air.arg_type_size", i32 4, !"air.arg_type_align_size", i32 4, !"air.arg_type_name", !"float", !"air.arg_name", !"weights"}
!4 = !{i32 1, !"air.buffer", !"air.location_index", i32 1, i32 1, !"air.write", !"air.address_space", i32 1, !"air.arg_type_size", i32 4, !"air.arg_type_align_size", i32 4, !"air.arg_type_name", !"float", !"air.arg_name", !"out"}
